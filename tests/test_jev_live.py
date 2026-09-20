"""Live contract tests against the real Jev API.

Skipped unless a key is configured, because most runs of this suite should not
need the network and must not cost anyone money. Enable with a configured key
plus:

    HELM_LIVE=1 python -m unittest tests.test_jev_live

These exist because the wire format was inferred for most of this project's
life and the honest thing was to label it unverified. It is verified now, and
these tests are what keep that claim true: they are the contract, and if
TypeSafe changes the API they fail here rather than silently degrading every
routing decision to the fallback scorer.

A full run costs well under a cent.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "helm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import jev  # noqa: E402
import route  # noqa: E402
import supervise as S  # noqa: E402

LIVE = os.environ.get("HELM_LIVE") == "1" and jev.available()
skip_unless_live = unittest.skipUnless(
    LIVE, "set HELM_LIVE=1 and configure a key to run live API tests")


@skip_unless_live
class TestWireContract(unittest.TestCase):
    def test_noul_returns_a_probability(self):
        r = jev.ask({"task": "delete the production database"},
                    {"risky": jev.noul("This task is dangerous")})
        noul = r["answers"]["risky"]["noul"]
        self.assertTrue(0.0 <= noul <= 1.0)
        self.assertGreater(noul, 0.5, "deleting prod should read as dangerous")

    def test_choice_returns_an_option_and_a_distribution(self):
        r = jev.ask({"task": "fix a crash in parser.py"},
                    {"kind": jev.choice("What kind of work is this?",
                                        {"bugfix": "fixing broken behaviour",
                                         "feature": "adding new behaviour"})})
        a = r["answers"]["kind"]
        self.assertEqual(a["choice"], "bugfix")
        # choice keys probabilities by option NAME
        self.assertEqual(set(a["probabilities"]), {"bugfix", "feature"})
        self.assertAlmostEqual(sum(a["probabilities"].values()), 1.0, places=2)

    def test_score_returns_a_continuous_value_with_an_index_legend(self):
        r = jev.ask({"task": "update a code comment"},
                    {"blast": jev.score("How much damage if wrong?",
                                        ["Comments or docs only",
                                         "A single tested function",
                                         "A shared module or public API",
                                         "Migrations, auth, billing, or infra"])})
        a = r["answers"]["blast"]
        self.assertTrue(0.0 <= a["score"] <= 3.0)
        self.assertLess(a["score"], 1.0, "a comment edit is the lowest level")
        # score keys probabilities by stringified INDEX, unlike choice
        self.assertEqual(set(a["probabilities"]), {"0", "1", "2", "3"})
        self.assertIn("legend", a)

    def test_response_reports_the_resolved_model_version(self):
        r = jev.ask({"x": "y"}, {"q": jev.noul("true")})
        self.assertEqual(r["model"], jev.MODEL)

    def test_usage_is_reported(self):
        r = jev.ask({"x": "y"}, {"q": jev.noul("true")})
        self.assertGreater(r["usage"]["input_tokens"], 0)

    def test_the_model_pin_is_actually_validated(self):
        # If a bogus version were silently accepted, the pin would be
        # decoration and every calibrated threshold would be unprotected.
        original = jev.MODEL
        jev.MODEL = "jev-9.99.9"
        try:
            with self.assertRaises(jev.JevUnavailable) as ctx:
                jev.ask({"x": "y"}, {"q": jev.noul("true")})
            self.assertIn("400", str(ctx.exception))
        finally:
            jev.MODEL = original

    def test_questions_are_answered_in_parallel_in_one_request(self):
        qs = {f"q{i}": jev.noul(f"Statement number {i} is true") for i in range(7)}
        r = jev.ask({"context": "arbitrary"}, qs)
        self.assertEqual(set(r["answers"]), set(qs))


@skip_unless_live
class TestRoutingEndToEnd(unittest.TestCase):
    AGENTS = [
        {"name": "aider", "competence": "Surgical edits to one or a few named files, "
         "committed to git automatically; needs the target identified up front",
         "strengths": [], "weaknesses": [], "context_class": "medium"},
        {"name": "cursor-agent", "competence": "Edits that first require finding every "
         "call site across an unfamiliar repository, using a semantic index",
         "strengths": [], "weaknesses": [], "context_class": "large"},
        {"name": "gemini", "competence": "Very large context sweeps: reading or "
         "summarising whole directories in a single pass",
         "strengths": [], "weaknesses": [], "context_class": "xlarge"},
    ]

    def _ask(self, intent):
        reg = {"hardware": {"os": "Linux", "cpu_logical_cores": 8, "ram_total_gb": 16.0,
                            "max_vram_gb": None},
               "local_inference": {"available": False}, "repo": None}
        state = route.build_state(intent, reg, self.AGENTS)
        return jev.ask(state, route.build_questions(self.AGENTS))

    def test_repo_wide_rename_prefers_the_semantic_indexer(self):
        r = self._ask("rename the Client class to ApiClient across the entire "
                      "repository and update every call site")
        self.assertEqual(r["answers"]["agent"]["choice"], "cursor-agent")

    def test_answer_space_is_respected(self):
        r = self._ask("do something vague")
        self.assertIn(r["answers"]["agent"]["choice"],
                      {a["name"] for a in self.AGENTS} | {"none"})

    def test_high_blast_radius_is_recognised(self):
        r = self._ask("drop the legacy users table and migrate authentication "
                      "to the new accounts schema")
        self.assertGreater(r["answers"]["blast_radius"]["score"], 2.0)

    def test_vague_request_scores_low_on_clarity(self):
        r = self._ask("make it better")
        self.assertLess(r["answers"]["spec_clarity"]["score"], 1.5)


@skip_unless_live
class TestCompletionJudgingEndToEnd(unittest.TestCase):
    """The cases an exit code cannot distinguish. All of these 'exit 0'."""

    TASK = ("add retry logic with exponential backoff to the payment client "
            "in src/payments/client.py")

    def _status(self, output):
        v = S.judge(self.TASK, "codex", output, 0, 30.0, False)
        self.assertEqual(v.get("source"), "jev", "expected a live Jev verdict")
        return v["answers"]["status"]["choice"]

    def test_real_work_reads_as_completed(self):
        self.assertEqual(self._status(
            "Reading src/payments/client.py...\n"
            "Added _retry_with_backoff() with 3 attempts and jitter.\n"
            "Edited src/payments/client.py (+34 -2)\nRan pytest: 18 passed.\n"),
            "completed")

    def test_describing_the_work_reads_as_no_op(self):
        self.assertEqual(self._status(
            "I've analyzed src/payments/client.py. Here's what I would do:\n"
            "1. Add a retry decorator\n2. Use exponential backoff\n"
            "This approach would handle transient failures well.\n"),
            "no_op")

    def test_asking_a_question_reads_as_blocked(self):
        self.assertEqual(self._status(
            "Before I proceed, should the retry use jitter? How many attempts "
            "do you want?\nLet me know and I'll implement it.\n"),
            "blocked_needs_input")

    def test_a_crash_reads_as_failed(self):
        v = S.judge(self.TASK, "codex",
                    "Traceback (most recent call last):\n"
                    "  File client.py, line 12\nPermissionError: [Errno 13] Denied\n",
                    1, 5.0, False)
        self.assertEqual(v["answers"]["status"]["choice"], "failed")

    def test_satisfied_discriminates_did_it_from_described_it(self):
        did = S.judge(self.TASK, "codex",
                      "Edited src/payments/client.py (+34 -2)\nRan pytest: 18 passed.",
                      0, 30.0, False)
        described = S.judge(self.TASK, "codex",
                            "Here's what I would do: add a retry decorator.",
                            0, 30.0, False)
        self.assertGreater(jev.answer(did, "task_satisfied", "noul"),
                           jev.answer(described, "task_satisfied", "noul") + 0.3)


@skip_unless_live
class TestConnectivityCheck(unittest.TestCase):
    def test_check_reports_ok_with_a_valid_key(self):
        r = jev.check()
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual(r["model_resolved"], jev.MODEL)

    def test_check_reports_failure_for_a_bad_key(self):
        original = os.environ.get("TYPESAFE_API_KEY")
        os.environ["TYPESAFE_API_KEY"] = "apikey_bogus_bogus"
        try:
            r = jev.check()
            self.assertFalse(r["ok"])
            self.assertEqual(r["stage"], "request")
        finally:
            if original is None:
                os.environ.pop("TYPESAFE_API_KEY", None)
            else:
                os.environ["TYPESAFE_API_KEY"] = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
