"""Tests for the decision layer.

`compose()` is a pure function from verdict to route, which is the whole point
of keeping L3 free of I/O: every threshold in the policy can be exercised here
without a machine probe or a network call.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "agent-router" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import probe  # noqa: E402
import route  # noqa: E402


def agent(name, *, competence="does things", fit=None, ctx="medium",
          verified=True, auth_ready=True, headless=True):
    return {
        "name": name,
        "display_name": name.title(),
        "installed": True,
        "path": f"/usr/bin/{name}",
        "status": "installed",
        "competence": competence,
        "strengths": [],
        "weaknesses": [],
        "task_fit": fit or {"feature": 4, "bugfix": 3, "refactor": 3, "other": 3},
        "context_class": ctx,
        "headless": {"argv": [name, "-p", "{prompt}"]} if headless else None,
        "contract_verified": verified,
        "requires": {},
        "auth": {"ready": auth_ready, "modes": [], "env_vars_set": [], "config_present": []},
        "routable": True,
        "routable_reason": "ok",
    }


def verdict(agent_name="claude", *, confidence=0.9, blast=1.0, clarity=3.0,
            breadth=1.0, needs_human=0.1, reversible=0.95, source="jev"):
    return {
        "source": source,
        "answers": {
            "task_kind": {"choice": "feature", "confidence": 0.8},
            "agent": {"choice": agent_name, "confidence": confidence,
                      "probabilities": {agent_name: confidence}},
            "blast_radius": {"score": blast, "confidence": 0.8},
            "spec_clarity": {"score": clarity, "confidence": 0.8},
            "context_breadth": {"score": breadth, "confidence": 0.8},
            "needs_human": {"noul": needs_human},
            "reversible": {"noul": reversible},
        },
    }


ROUTABLE = [agent("claude", ctx="xlarge"), agent("codex"), agent("aider", ctx="medium")]


class TestGates(unittest.TestCase):
    """Each threshold should block automation on its own."""

    def test_clean_verdict_auto_executes(self):
        r = route.compose(verdict(), ROUTABLE, "add a flag")
        self.assertEqual(r["mode"], "auto")
        self.assertEqual(r["agent"], "claude")
        self.assertEqual(r["gates"], [])

    def test_low_confidence_blocks(self):
        r = route.compose(verdict(confidence=0.5), ROUTABLE, "add a flag")
        self.assertEqual(r["mode"], "recommend")
        self.assertTrue(any("confidence" in g for g in r["gates"]))

    def test_high_blast_radius_blocks(self):
        r = route.compose(verdict(blast=3.5), ROUTABLE, "drop a table")
        self.assertEqual(r["mode"], "recommend")
        self.assertTrue(any("blast_radius" in g for g in r["gates"]))

    def test_needs_human_blocks(self):
        r = route.compose(verdict(needs_human=0.8), ROUTABLE, "rotate the keys")
        self.assertTrue(any("needs_human" in g for g in r["gates"]))

    def test_irreversible_blocks(self):
        r = route.compose(verdict(reversible=0.2), ROUTABLE, "deploy to prod")
        self.assertTrue(any("reversible" in g for g in r["gates"]))

    def test_low_clarity_asks_first(self):
        r = route.compose(verdict(clarity=0.5), ROUTABLE, "make it better")
        self.assertEqual(r["mode"], "clarify")

    def test_boundary_is_inclusive_where_documented(self):
        # clarity exactly at the threshold should still clarify (<=), while
        # confidence exactly at its threshold should pass (>=).
        self.assertEqual(
            route.compose(verdict(clarity=1.0), ROUTABLE, "x")["mode"], "clarify")
        self.assertEqual(
            route.compose(verdict(confidence=0.75), ROUTABLE, "x")["mode"], "auto")

    def test_gates_accumulate(self):
        r = route.compose(
            verdict(confidence=0.4, blast=3.5, needs_human=0.9, reversible=0.1),
            ROUTABLE, "drop the users table")
        self.assertGreaterEqual(len(r["gates"]), 4)
        self.assertEqual(r["mode"], "recommend")


class TestAnswerSpaceIntegrity(unittest.TestCase):
    """The core safety property: never recommend something that is not here."""

    def test_none_escalates(self):
        r = route.compose(verdict("none"), ROUTABLE, "cure cancer")
        self.assertEqual(r["mode"], "escalate")
        self.assertIsNone(r["agent"])

    def test_unknown_agent_escalates_rather_than_routing(self):
        # Unreachable through a Jev Choice by construction; this is the
        # defence-in-depth path if a future edit widens the answer space.
        r = route.compose(verdict("cursor-agent"), ROUTABLE, "refactor")
        self.assertEqual(r["mode"], "escalate")
        self.assertTrue(any("not in the routable set" in g for g in r["gates"]))

    def test_agent_question_only_offers_installed_agents(self):
        q = route.build_agent_question(ROUTABLE)
        self.assertEqual(set(q["criteria"]) - {"none"}, {"claude", "codex", "aider"})

    def test_agent_question_always_has_an_escape_hatch(self):
        self.assertIn("none", route.build_agent_question(ROUTABLE)["criteria"])
        self.assertIn("none", route.build_agent_question([])["criteria"])


class TestFallback(unittest.TestCase):
    """A degraded judge may suggest, never act unattended."""

    def test_fallback_never_auto_executes(self):
        r = route.compose(verdict(confidence=0.99, source="fallback"), ROUTABLE, "x")
        self.assertNotEqual(r["mode"], "auto")
        self.assertTrue(any("fallback" in g for g in r["gates"]))

    def test_fallback_confidence_is_capped(self):
        r = route.compose(verdict(confidence=0.99, source="fallback"), ROUTABLE, "x")
        self.assertLessEqual(r["signals"]["confidence"],
                             route.THRESHOLDS["FALLBACK_CONFIDENCE_CAP"])

    def test_fallback_picks_only_from_routable(self):
        v = route.fallback_verdict("fix the failing test", ROUTABLE, None)
        self.assertIn(v["answers"]["agent"]["choice"], {a["name"] for a in ROUTABLE})

    def test_fallback_probabilities_are_a_distribution(self):
        v = route.fallback_verdict("add a feature", ROUTABLE, None)
        probs = v["answers"]["agent"]["probabilities"]
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=2)
        self.assertTrue(all(0.0 <= p <= 1.0 for p in probs.values()))

    def test_fallback_handles_empty_agent_list(self):
        v = route.fallback_verdict("anything", [], None)
        self.assertIn("error", v)

    def test_clarity_rewards_concrete_referents_not_length(self):
        clarify_at = route.THRESHOLDS["CLARIFY_MAX_SPEC_CLARITY"]
        # Short but concrete: names a function and a file, so it is actionable.
        self.assertGreater(
            route.estimate_clarity("fix the off-by-one in parse_header in src/http/headers.py"),
            clarify_at)
        # Longer but vague: no referent to act on.
        for vague in ("make it better", "add dark mode", "clean this up a bit please"):
            with self.subTest(vague=vague):
                self.assertLessEqual(route.estimate_clarity(vague), clarify_at)

    def test_clarity_stays_in_range(self):
        for text in ("x", "make it better", "a " * 200,
                     'rename Client to ApiClient in src/api.py so that "the name" matches'):
            self.assertTrue(0.0 <= route.estimate_clarity(text) <= 3.0)

    def test_context_penalty_does_not_swamp_a_specialist(self):
        # A medium-context specialist with a clearly better task_fit should beat
        # a large-context generalist on a narrow task. Rounding breadth to an
        # integer rank used to lose this.
        specialist = agent("copilot", ctx="medium", fit={**{k: 2 for k in route.TASK_KINDS},
                                                         "review": 5})
        generalist = agent("codex", ctx="large", fit={**{k: 2 for k in route.TASK_KINDS},
                                                      "review": 4})
        v = route.fallback_verdict("review the open pull request", [specialist, generalist], None)
        self.assertEqual(v["answers"]["agent"]["choice"], "copilot")

    def test_classify_kind(self):
        cases = {
            "fix the failing test in parser.py": "test",
            "the login button crashes on submit": "bugfix",
            "rename Client to ApiClient everywhere": "refactor",
            "update the README install steps": "docs",
            "set up a new service from scratch": "scaffold",
            "add dark mode support": "feature",
            "write a github actions deploy pipeline": "ops",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(route.classify_kind(text), expected)


class TestCommandRendering(unittest.TestCase):
    def test_prompt_substitution_is_a_single_argv_element(self):
        cmd = route.render_command(
            {"headless": {"argv": ["aider", "--message", "{prompt}", "--yes-always"]}},
            'add "quotes" & an ampersand',
        )
        self.assertEqual(cmd[2], 'add "quotes" & an ampersand')
        self.assertEqual(len(cmd), 4)

    def test_agent_with_no_contract_renders_nothing(self):
        self.assertEqual(route.render_command({"headless": None}, "x"), [])


class TestRoutableFiltering(unittest.TestCase):
    NO_LOCAL = {"available": False, "vram_gb": 6.0}
    WITH_LOCAL = {"available": True, "vram_gb": 24.0}

    def test_absent_agent_is_not_routable(self):
        a = agent("ghost")
        a["installed"] = False
        self.assertFalse(probe.routable(a, self.NO_LOCAL)[0])

    def test_missing_headless_contract_is_not_routable(self):
        ok, why = probe.routable(agent("kiro", headless=False), self.NO_LOCAL)
        self.assertFalse(ok)
        self.assertIn("headless", why)

    def test_undetected_credentials_do_not_disqualify(self):
        # Credentials often live in an OS keychain we cannot enumerate, so
        # "not found" must not be treated as "not present".
        ok, _ = probe.routable(agent("cursor-agent", auth_ready=False), self.NO_LOCAL)
        self.assertTrue(ok)

    def test_local_model_needs_a_runtime_and_weights(self):
        a = agent("ollama")
        a["requires"] = {"local_inference": True}
        self.assertFalse(probe.routable(a, self.NO_LOCAL)[0])
        self.assertTrue(probe.routable(a, self.WITH_LOCAL)[0])

    def test_vram_requirement_is_enforced(self):
        a = agent("ollama")
        a["requires"] = {"local_inference": True, "min_vram_gb": 16.0}
        ok, why = probe.routable(a, {"available": True, "vram_gb": 6.0})
        self.assertFalse(ok)
        self.assertIn("VRAM", why)

    def test_degraded_agent_is_not_routable(self):
        a = agent("slowpoke")
        a["status"] = "degraded"
        self.assertFalse(probe.routable(a, self.NO_LOCAL)[0])


class TestCards(unittest.TestCase):
    def test_every_card_is_valid_and_complete(self):
        cards = probe.load_cards()
        self.assertGreaterEqual(len(cards), 7)
        for c in cards:
            with self.subTest(card=c.get("name")):
                for field in ("name", "display_name", "competence", "task_fit",
                              "context_class", "bin_names"):
                    self.assertIn(field, c)
                self.assertTrue(c["competence"].strip())
                self.assertIn(c["context_class"], {"small", "medium", "large", "xlarge"})
                for kind in route.TASK_KINDS:
                    self.assertIn(kind, c["task_fit"], f"{c['name']} missing fit for {kind}")
                    self.assertIn(c["task_fit"][kind], range(0, 6))
                if c.get("headless"):
                    self.assertIn("{prompt}", json.dumps(c["headless"]["argv"]))

    def test_competences_are_distinct(self):
        # Identical competence lines would make the Choice undecidable.
        lines = [c["competence"] for c in probe.load_cards()]
        self.assertEqual(len(lines), len(set(lines)))


class TestStateHygiene(unittest.TestCase):
    def test_state_carries_no_secrets_and_stays_small(self):
        reg = {
            "hardware": {"os": "Linux", "cpu_logical_cores": 8, "ram_total_gb": 16.0,
                         "max_vram_gb": None},
            "local_inference": {"available": False},
            "repo": {"root": "/tmp/x", "languages": ["python"], "file_count": 10,
                     "has_tests": True, "vcs": "git", "branch": "main", "dirty": False},
        }
        state = route.build_state("do a thing", reg, ROUTABLE)
        blob = json.dumps(state).lower()
        for leak in ("api_key", "token", "secret", "password", "authorization"):
            self.assertNotIn(leak, blob)
        self.assertLess(len(blob), 8000)  # far inside Jev's 32k state budget

    def test_state_lists_only_routable_agents(self):
        reg = {"hardware": {"os": "x", "cpu_logical_cores": 1, "ram_total_gb": 1.0,
                            "max_vram_gb": None},
               "local_inference": {"available": False}, "repo": None}
        names = {a["name"] for a in route.build_state("x", reg, ROUTABLE)["agents"]}
        self.assertEqual(names, {"claude", "codex", "aider"})


class TestTraces(unittest.TestCase):
    def test_trace_row_is_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            original = route.TRACE_PATH
            route.TRACE_PATH = Path(td) / "decisions.jsonl"
            try:
                reg = {"agents": ROUTABLE}
                v = verdict()
                route.write_trace("x", reg, v, route.compose(v, ROUTABLE, "x"))
                rows = route.TRACE_PATH.read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(rows), 1)
                row = json.loads(rows[0])
                self.assertEqual(row["intent"], "x")
                self.assertIn("thresholds", row)
                self.assertIn("answers", row)
            finally:
                route.TRACE_PATH = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
