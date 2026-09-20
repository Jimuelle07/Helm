"""Tests for the Jev-metrics -> agent-native-invocation translation.

The property worth protecting: a metric must never widen an agent's
permissions by accident, and a metric nobody supplied must change nothing at
all. Most of what follows is about the second half of that -- silence has to
degrade to the previous behaviour, because the alternative is a system that
quietly starts running `--yolo` the day someone forgets to pass a signal.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dispatch as D  # noqa: E402
import probe  # noqa: E402

CARDS = {c["name"]: c for c in probe.load_cards()}


def card(name="test", *, argv=None, modes=None, recovery=None):
    """A minimal card, so rule tests do not depend on a real agent's flags."""
    headless = {"argv": argv or ["test", "{flags}", "-p", "{prompt}"]}
    if modes is not None:
        headless["modes"] = modes
    if recovery is not None:
        headless["recovery"] = recovery
    return {"name": name, "headless": headless}


FULL_MODES = {
    "unattended": {"args": ["--yolo"], "verified": True},
    "sandbox": {"args": ["-w"], "verified": True},
    "plan": {"args": ["--architect"], "verified": True},
    "parallel": {"prompt_prefix": "Fan out subagents in parallel to ", "verified": True},
    "readonly": {"args": ["--dry-run"], "verified": True},
}


# =========================================================================== #
# The four rules the goal specifies
# =========================================================================== #

class TestRules(unittest.TestCase):
    def plan(self, sig, modes=FULL_MODES, task="do the thing"):
        return D.plan(card(modes=modes), task, sig)

    def test_rule1_low_needs_human_goes_unattended(self):
        p = self.plan({"needs_human": 0.1})
        self.assertIn("unattended", p.modes)
        self.assertIn("--yolo", p.argv)

    def test_rule1_does_not_fire_at_the_threshold(self):
        # 0.30 is the documented boundary and the rule is strictly less-than.
        self.assertNotIn("unattended", self.plan({"needs_human": 0.30}).modes)
        self.assertIn("unattended", self.plan({"needs_human": 0.29}).modes)

    def test_rule2_high_blast_radius_forces_a_sandbox(self):
        p = self.plan({"blast_radius": 2.5})
        self.assertIn("sandbox", p.modes)
        self.assertIn("-w", p.argv)

    def test_rule2_does_not_fire_at_the_threshold(self):
        self.assertNotIn("sandbox", self.plan({"blast_radius": 2.0}).modes)
        self.assertIn("sandbox", self.plan({"blast_radius": 2.01}).modes)

    def test_rule3_scaffold_plans_before_coding(self):
        p = self.plan({"task_kind": "scaffold"})
        self.assertIn("plan", p.modes)
        self.assertIn("--architect", p.argv)

    def test_rule4_wide_refactor_fans_out(self):
        p = self.plan({"task_kind": "refactor", "context_breadth": 3.0})
        self.assertIn("parallel", p.modes)
        self.assertTrue(p.prompt.startswith("Fan out subagents in parallel to "))

    def test_rule4_needs_both_halves(self):
        # A narrow refactor is not a fan-out job, and a wide *feature* is a
        # different shape of work -- the rule is the conjunction, not either.
        self.assertNotIn("parallel",
                         self.plan({"task_kind": "refactor", "context_breadth": 1.0}).modes)
        self.assertNotIn("parallel",
                         self.plan({"task_kind": "feature", "context_breadth": 3.0}).modes)

    def test_review_and_research_are_read_only(self):
        for kind in ("review", "research"):
            with self.subTest(kind=kind):
                p = self.plan({"task_kind": kind})
                self.assertIn("readonly", p.modes)
                self.assertIn("--dry-run", p.argv)

    def test_rules_compose(self):
        p = self.plan({"task_kind": "refactor", "context_breadth": 3.0,
                       "blast_radius": 3.0, "needs_human": 0.05})
        self.assertEqual(set(p.modes), {"sandbox", "parallel", "unattended"})


# =========================================================================== #
# Silence must not change anything
# =========================================================================== #

class TestNoSignalsIsNoChange(unittest.TestCase):
    """The regression that would matter most: metrics nobody passed must not
    start granting permissions on their own."""

    def test_empty_signals_render_the_base_contract(self):
        c = card(modes=FULL_MODES)
        self.assertEqual(D.plan(c, "task", None).argv, D.base_plan(c, "task").argv)
        self.assertEqual(D.plan(c, "task", {}).argv, D.base_plan(c, "task").argv)

    def test_partial_signals_only_fire_their_own_rules(self):
        p = D.plan(card(modes=FULL_MODES), "task", {"needs_human": 0.1})
        self.assertEqual(p.modes, ("unattended",))

    def test_unknown_task_kind_fires_nothing(self):
        self.assertEqual(D.plan(card(modes=FULL_MODES), "t", {"task_kind": "ops"}).modes, ())

    def test_every_real_card_is_unchanged_without_signals(self):
        for name, c in CARDS.items():
            if not (c.get("headless") or {}).get("argv"):
                continue
            with self.subTest(agent=name):
                self.assertEqual(D.plan(c, "t", None).argv, D.base_plan(c, "t").argv)


# =========================================================================== #
# Conflicts and the safety interaction
# =========================================================================== #

class TestConflicts(unittest.TestCase):
    def test_readonly_cancels_unattended(self):
        # Widening write permission for a task that must not write is not a
        # trade-off to balance, it is a contradiction.
        p = D.plan(card(modes=FULL_MODES), "t",
                   {"task_kind": "review", "needs_human": 0.05})
        self.assertIn("readonly", p.modes)
        self.assertNotIn("unattended", p.modes)
        self.assertNotIn("--yolo", p.argv)

    def test_unattended_is_withheld_when_the_sandbox_is_unavailable(self):
        modes = {k: v for k, v in FULL_MODES.items() if k != "sandbox"}
        p = D.plan(card(modes=modes), "t", {"needs_human": 0.05, "blast_radius": 3.0})
        self.assertNotIn("unattended", p.modes)
        self.assertNotIn("--yolo", p.argv)
        self.assertIn("sandbox", p.unmet)
        self.assertTrue(any("withheld unattended" in n for n in p.notes))

    def test_unattended_survives_a_missing_sandbox_when_blast_is_low(self):
        modes = {k: v for k, v in FULL_MODES.items() if k != "sandbox"}
        p = D.plan(card(modes=modes), "t", {"needs_human": 0.05, "blast_radius": 1.0})
        self.assertIn("unattended", p.modes)

    def test_a_base_contract_guarantee_is_reported_not_pretended_away(self):
        # Aider's --yes-always cannot be withheld -- stripping it would make
        # the run hang, not make it safe. Claiming a protection we did not
        # apply would be worse than saying so.
        modes = {"unattended": {"satisfied_by_base": True, "verified": True}}
        p = D.plan(card(name="aiderish", modes=modes), "t",
                   {"needs_human": 0.05, "blast_radius": 3.0})
        self.assertIn("unattended", p.modes)
        self.assertFalse(any("withheld" in n for n in p.notes))
        self.assertTrue(any("cannot be made to pause" in n for n in p.notes))


# =========================================================================== #
# Rendering
# =========================================================================== #

class TestRendering(unittest.TestCase):
    def test_prompt_stays_one_argv_element_even_with_a_prefix(self):
        # The injection guarantee the schema doc makes has to survive the
        # decoration, or a prefix becomes a way to smuggle in a second token.
        p = D.plan(card(modes=FULL_MODES), 'add "quotes" & an ampersand',
                   {"task_kind": "refactor", "context_breadth": 3.0})
        self.assertEqual(sum(1 for t in p.argv if "ampersand" in t), 1)
        self.assertIn('add "quotes" & an ampersand', p.argv[-1])

    def test_flags_are_spliced_not_stringified(self):
        p = D.plan(card(modes=FULL_MODES), "t", {"blast_radius": 3.0})
        self.assertIn("-w", p.argv)
        self.assertNotIn("{flags}", p.argv)

    def test_the_flags_placeholder_never_survives(self):
        for sig in (None, {"needs_human": 0.1}, {"task_kind": "review"}):
            with self.subTest(sig=sig):
                self.assertNotIn("{flags}", D.plan(card(modes=FULL_MODES), "t", sig).argv)

    def test_replaces_strips_the_old_flag_and_its_value(self):
        c = card(argv=["claude", "--permission-mode", "acceptEdits", "{flags}", "{prompt}"],
                 modes={"unattended": {"args": ["--permission-mode", "bypassPermissions"],
                                       "replaces": ["--permission-mode"], "verified": True}})
        argv = D.plan(c, "t", {"needs_human": 0.1}).argv
        self.assertEqual(argv.count("--permission-mode"), 1)
        self.assertIn("bypassPermissions", argv)
        self.assertNotIn("acceptEdits", argv)

    def test_replacing_a_boolean_flag_does_not_swallow_the_next_one(self):
        c = card(argv=["cli", "--allow-all-tools", "--verbose", "{flags}", "{prompt}"],
                 modes={"unattended": {"args": ["--allow-all"],
                                       "replaces": ["--allow-all-tools"], "verified": True}})
        argv = D.plan(c, "t", {"needs_human": 0.1}).argv
        self.assertIn("--verbose", argv)
        self.assertNotIn("--allow-all-tools", argv)

    def test_a_card_without_a_flags_slot_drops_flags_rather_than_guessing(self):
        c = card(argv=["cli", "-p", "{prompt}"], modes=FULL_MODES)
        p = D.plan(c, "t", {"needs_human": 0.1})
        self.assertNotIn("--yolo", p.argv)
        self.assertIn("unattended", p.unmet)
        self.assertTrue(any("no {flags} slot" in n for n in p.notes))

    def test_a_missing_flags_slot_does_not_strip_the_flag_it_meant_to_replace(self):
        # Removing acceptEdits without adding bypassPermissions would leave
        # the agent on its own default -- the one outcome nobody asked for.
        c = card(argv=["claude", "--permission-mode", "acceptEdits", "{prompt}"],
                 modes={"unattended": {"args": ["--permission-mode", "bypassPermissions"],
                                       "replaces": ["--permission-mode"], "verified": True}})
        argv = D.plan(c, "t", {"needs_human": 0.1}).argv
        self.assertIn("acceptEdits", argv)

    def test_a_bad_replaces_cannot_swallow_the_task(self):
        # A card naming a prompt-bearing flag in `replaces` is a typo, and the
        # symptom would be an agent launched with no instructions and a clean
        # exit -- the exact silent no-op the supervisor exists to catch.
        c = card(argv=["aider", "{flags}", "--message", "{prompt}", "--yes-always"],
                 modes={"unattended": {"args": ["--force"],
                                       "replaces": ["--message"], "verified": True}})
        argv = D.plan(c, "fix the parser", {"needs_human": 0.1}).argv
        self.assertIn("fix the parser", argv)

    def test_planning_is_deterministic(self):
        sig = {"task_kind": "refactor", "context_breadth": 3.0,
               "blast_radius": 3.0, "needs_human": 0.05}
        runs = {tuple(D.plan(card(modes=FULL_MODES), "t", sig).argv) for _ in range(5)}
        self.assertEqual(len(runs), 1)

    def test_unverified_flags_are_reported(self):
        c = card(modes={"unattended": {"args": ["-f"], "verified": False}})
        self.assertEqual(D.plan(c, "t", {"needs_human": 0.1}).unverified, ("unattended",))

    def test_a_card_with_no_contract_plans_nothing(self):
        self.assertEqual(D.plan({"name": "x", "headless": None}, "t", {}).argv, [])


# =========================================================================== #
# Signals parsing
# =========================================================================== #

class TestEnforcedVsAdvisory(unittest.TestCase):
    """"Do not write any files", which the model may ignore, is not the same
    guarantee as a flag that makes writing impossible. A caller reading
    `modes: readonly` is entitled to know which one they got."""

    def test_a_flag_is_enforcement(self):
        self.assertTrue(D.is_enforced({"args": ["--dry-run"]}))

    def test_a_base_contract_guarantee_is_enforcement(self):
        self.assertTrue(D.is_enforced({"satisfied_by_base": True}))

    def test_a_prompt_prefix_is_only_advice(self):
        # Including a slash directive: it is only as reliable as the agent's
        # own handling of it, which nothing here can check.
        self.assertFalse(D.is_enforced({"prompt_prefix": "/plan "}))

    def test_an_advisory_readonly_is_called_out(self):
        c = card(modes={"readonly": {"prompt_prefix": "Do not write. ",
                                     "verified": True, "source": "sheet"}})
        p = D.plan(c, "t", {"task_kind": "review"})
        self.assertIn("readonly", p.modes)
        self.assertIn("readonly", p.advisory)
        self.assertTrue(any("ADVISORY" in n for n in p.notes))

    def test_an_enforced_readonly_is_not_flagged(self):
        c = card(modes={"readonly": {"args": ["--dry-run"], "verified": True,
                                     "source": "--help"}})
        p = D.plan(c, "t", {"task_kind": "review"})
        self.assertEqual(p.advisory, ())
        self.assertFalse(any("ADVISORY" in n for n in p.notes))

    def test_advisory_only_lists_modes_that_were_actually_applied(self):
        # A mode withheld by the sandbox interaction must not still be
        # reported as something the run is relying on.
        modes = {"unattended": {"prompt_prefix": "Go wild. ", "verified": True,
                                "source": "sheet"}}
        p = D.plan(card(modes=modes), "t", {"needs_human": 0.05, "blast_radius": 3.0})
        self.assertNotIn("unattended", p.modes)
        self.assertNotIn("unattended", p.advisory)

    def test_a_quality_mode_being_advisory_is_not_shouted_about(self):
        # parallel and plan are advice by nature; only the safety modes get
        # the loud note, or the signal stops meaning anything.
        c = card(modes={"parallel": {"prompt_prefix": "Fan out. ", "verified": True,
                                     "source": "sheet"}})
        p = D.plan(c, "t", {"task_kind": "refactor", "context_breadth": 3.0})
        self.assertIn("parallel", p.advisory)
        self.assertFalse(any("ADVISORY" in n for n in p.notes))


class TestGoosePlanIsReadOnly(unittest.TestCase):
    """Regression for the bug the cheat sheet caught.

    Goose's /plan locks the agent into a read-only phase until /endplan. A
    one-shot `goose run` never gets a second turn to send /endplan from, so
    using /plan for plan-then-build would make every scaffold task a
    guaranteed no_op.
    """

    def setUp(self):
        self.goose = CARDS["goose"]

    def test_scaffold_does_not_get_the_plan_directive(self):
        p = D.plan(self.goose, "build a service", {"task_kind": "scaffold"})
        self.assertIn("plan", p.modes)
        self.assertNotIn("/plan", p.prompt)

    def test_review_does_get_the_plan_directive(self):
        p = D.plan(self.goose, "review this", {"task_kind": "review"})
        self.assertIn("readonly", p.modes)
        self.assertTrue(p.prompt.startswith("/plan "))

    def test_no_card_uses_a_locking_directive_for_the_plan_mode(self):
        # The general trap: a directive that is correct interactively can be
        # exactly wrong headless, because the turn that releases it never comes.
        for name, c in CARDS.items():
            spec = D.card_modes(c).get("plan") or {}
            with self.subTest(agent=name):
                self.assertNotIn("/plan", spec.get("prompt_prefix", ""))


class TestSignals(unittest.TestCase):
    def test_reads_a_flat_signals_block(self):
        s = D.Signals.from_dict({"task_kind": "refactor", "blast_radius": 2.5})
        self.assertEqual((s.task_kind, s.blast_radius), ("refactor", 2.5))

    def test_reads_a_raw_jev_verdict(self):
        s = D.Signals.from_dict({"answers": {
            "task_kind": {"choice": "scaffold", "confidence": 0.9},
            "blast_radius": {"score": 3.0},
            "needs_human": {"noul": 0.1},
        }})
        self.assertEqual(s.task_kind, "scaffold")
        self.assertEqual(s.blast_radius, 3.0)
        self.assertEqual(s.needs_human, 0.1)

    def test_flat_keys_win_over_nested_answers(self):
        s = D.Signals.from_dict({"blast_radius": 1.0,
                                 "answers": {"blast_radius": {"score": 3.0}}})
        self.assertEqual(s.blast_radius, 1.0)

    def test_junk_values_become_none_rather_than_crashing(self):
        s = D.Signals.from_dict({"blast_radius": "high", "task_kind": 7})
        self.assertIsNone(s.blast_radius)
        self.assertIsNone(s.task_kind)

    def test_empty_detects_the_no_information_case(self):
        self.assertTrue(D.Signals.from_dict(None).empty())
        self.assertTrue(D.Signals.from_dict({"confidence": 0.9}).empty())
        self.assertFalse(D.Signals.from_dict({"needs_human": 0.1}).empty())


# =========================================================================== #
# Recovery
# =========================================================================== #

RECOVERY = {
    "no_op": {"prompt_prefix": "Apply it now: ", "modes": ["unattended"]},
    "stuck": {"prompt_prefix": "Do not ask: "},
    "failed": {"prompt_prefix": "Retrying: ", "cleanup": ["cli", "--message", "/undo"]},
}


class TestRecovery(unittest.TestCase):
    def setUp(self):
        self.c = card(modes=FULL_MODES, recovery=RECOVERY)

    def test_recovery_is_found_for_the_declared_outcomes(self):
        for outcome in ("no_op", "stuck", "failed"):
            with self.subTest(outcome=outcome):
                self.assertIsNotNone(D.recovery_for(self.c, outcome))

    def test_a_clean_outcome_has_no_recovery(self):
        for outcome in ("done", "uncertain", "incomplete", "error"):
            with self.subTest(outcome=outcome):
                self.assertIsNone(D.recovery_for(self.c, outcome))

    def test_timeout_is_deliberately_not_recoverable(self):
        # A killed run may have left the tree half-modified, and re-dispatching
        # onto unknown partial state turns one bad run into two.
        self.assertNotIn("timeout", D.RECOVERABLE)
        self.assertIsNone(D.recovery_for(self.c, "timeout"))

    def test_a_card_without_a_recovery_table_gets_none(self):
        self.assertIsNone(D.recovery_for(card(modes=FULL_MODES), "no_op"))

    def test_recovery_prefixes_the_task_and_forces_its_modes(self):
        rec = D.recovery_for(self.c, "no_op")
        p = D.apply_recovery(self.c, "fix the parser", rec, None)
        self.assertTrue(p.prompt.endswith("fix the parser"))
        self.assertIn("Apply it now: ", p.prompt)
        self.assertIn("unattended", p.modes)

    def test_recovery_modes_stack_on_top_of_the_metric_driven_ones(self):
        rec = D.recovery_for(self.c, "no_op")
        p = D.apply_recovery(self.c, "t", rec, {"blast_radius": 3.0})
        self.assertEqual(set(p.modes), {"sandbox", "unattended"})

    def test_cleanup_is_carried_through(self):
        self.assertEqual(D.recovery_for(self.c, "failed").cleanup,
                         ["cli", "--message", "/undo"])


class TestShouldRecover(unittest.TestCase):
    def setUp(self):
        self.rec = D.recovery_for(card(modes=FULL_MODES, recovery=RECOVERY), "no_op")

    def test_a_jev_verdict_within_budget_recovers(self):
        go, _ = D.should_recover({"judged_by": "jev"}, self.rec, 0, 1)
        self.assertTrue(go)

    def test_an_unjudged_verdict_never_triggers_a_retry(self):
        # The standing rule in this codebase: only a Jev verdict may drive an
        # action. An `error`/`unjudged` run is one nobody has assessed, so
        # re-running it over the same workspace is the last thing to do.
        for judged_by in ("precondition", None):
            with self.subTest(judged_by=judged_by):
                go, why = D.should_recover({"judged_by": judged_by}, self.rec, 0, 1)
                self.assertFalse(go)
                self.assertIn("never judged by Jev", why)

    def test_the_budget_is_respected(self):
        go, why = D.should_recover({"judged_by": "jev"}, self.rec, 1, 1)
        self.assertFalse(go)
        self.assertIn("budget", why)

    def test_no_declared_recovery_means_no_retry(self):
        go, why = D.should_recover({"judged_by": "jev"}, None, 0, 1)
        self.assertFalse(go)
        self.assertIn("no recovery", why)


# =========================================================================== #
# The cards themselves
# =========================================================================== #

class TestCardModeTables(unittest.TestCase):
    def test_every_declared_mode_is_in_the_vocabulary(self):
        # A typo'd mode name is invisible at runtime: the planner simply never
        # finds it and reports the agent as unable to express the mode.
        for name, c in CARDS.items():
            for mode in D.card_modes(c):
                with self.subTest(agent=name, mode=mode):
                    self.assertIn(mode, D.MODES)

    def test_every_mode_declares_how_it_works(self):
        for name, c in CARDS.items():
            for mode, spec in D.card_modes(c).items():
                with self.subTest(agent=name, mode=mode):
                    self.assertTrue(
                        spec.get("args") or spec.get("prompt_prefix")
                        or spec.get("satisfied_by_base"),
                        "a mode must inject flags, prefix the prompt, or say "
                        "the base contract already covers it")
                    self.assertIn("verified", spec)
                    self.assertIsInstance(spec["verified"], bool)

    def test_every_mode_records_where_it_came_from(self):
        for name, c in CARDS.items():
            for mode, spec in D.card_modes(c).items():
                with self.subTest(agent=name, mode=mode):
                    self.assertTrue(spec.get("source"),
                                    "a mode must say whether its flags came from "
                                    "the CLI's --help or from the cheat sheet")

    def test_verified_flags_were_read_off_the_cli_not_a_document(self):
        # The invariant that keeps `verified` meaning one thing. A published
        # cheat sheet is a better source than a guess -- it corrected two of
        # these cards -- but it is not the binary, and this repo has already
        # caught the sheet describing a flag the installed CLI does not have.
        for name, c in CARDS.items():
            for mode, spec in D.card_modes(c).items():
                if spec.get("args") and spec.get("verified"):
                    with self.subTest(agent=name, mode=mode):
                        self.assertIn("--help", str(spec.get("source", "")))

    def test_cards_with_flag_modes_have_a_flags_slot(self):
        for name, c in CARDS.items():
            argv = (c.get("headless") or {}).get("argv") or []
            needs_slot = any(spec.get("args") for spec in D.card_modes(c).values())
            with self.subTest(agent=name):
                if needs_slot:
                    self.assertIn("{flags}", argv)

    def test_recovery_outcomes_are_all_recoverable_ones(self):
        for name, c in CARDS.items():
            table = (c.get("headless") or {}).get("recovery") or {}
            for outcome in table:
                with self.subTest(agent=name, outcome=outcome):
                    self.assertIn(outcome, D.RECOVERABLE)

    def test_recovery_modes_are_modes_the_card_actually_has(self):
        for name, c in CARDS.items():
            have = D.card_modes(c)
            table = (c.get("headless") or {}).get("recovery") or {}
            for outcome, spec in table.items():
                for mode in spec.get("modes") or []:
                    with self.subTest(agent=name, outcome=outcome, mode=mode):
                        self.assertIn(mode, have)

    def test_every_card_still_renders_a_runnable_command(self):
        for name, c in CARDS.items():
            if not (c.get("headless") or {}).get("argv"):
                continue
            with self.subTest(agent=name):
                argv = D.plan(c, "do a thing", {"needs_human": 0.1,
                                                "blast_radius": 3.0,
                                                "task_kind": "scaffold"}).argv
                self.assertTrue(argv)
                self.assertFalse([t for t in argv if t.startswith("{") and t.endswith("}")
                                  and t != "{model}"],
                                 f"{name} left an unsubstituted placeholder: {argv}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
