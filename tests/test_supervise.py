"""Tests for Jev-judged completion.

The property worth protecting here: an exit code cannot tell "wrote the code"
from "wrote about the code". Several of these cases exit 0 and must still
produce different verdicts, because that distinction is the entire reason a
semantic judge is worth a network round-trip.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "helm" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import supervise as S  # noqa: E402

LOG = Path("run.log")


def verdict(status="completed", *, confidence=0.9, satisfied=0.9, needs_human=0.1,
            awaiting=0.02, severity=0.3, source="jev"):
    return {
        "source": source,
        "answers": {
            "status": {"choice": status, "confidence": confidence},
            "task_satisfied": {"noul": satisfied},
            "needs_human": {"noul": needs_human},
            "awaiting_input": {"noul": awaiting},
            "failure_severity": {"score": severity},
        },
    }


def compose(v, rc=0, elapsed=10.0, timed_out=False, output="did the thing properly"):
    return S.compose(v, rc, elapsed, timed_out, "codex", LOG, output)


class TestExitCodeIsNotEnough(unittest.TestCase):
    """Every case here exits 0. They must not all look the same."""

    def test_clean_success_is_accepted(self):
        r = compose(verdict())
        self.assertEqual((r["outcome"], r["next"]), ("done", "accept"))

    def test_no_op_is_caught_despite_success_exit(self):
        r = compose(verdict("no_op", satisfied=0.1))
        self.assertEqual((r["outcome"], r["next"]), ("no_op", "retry"))

    def test_agent_waiting_for_input_is_caught(self):
        r = compose(verdict("blocked_needs_input", satisfied=0.2, awaiting=0.92))
        self.assertEqual((r["outcome"], r["next"]), ("stuck", "ask_user"))

    def test_claims_done_but_did_not_satisfy_the_task(self):
        r = compose(verdict("completed", satisfied=0.3))
        self.assertEqual(r["outcome"], "incomplete")
        self.assertEqual(r["next"], "review")

    def test_done_but_low_confidence_gets_a_second_look(self):
        r = compose(verdict("completed", confidence=0.5))
        self.assertEqual((r["outcome"], r["next"]), ("uncertain", "review"))

    def test_partial_work_is_retried(self):
        self.assertEqual(compose(verdict("partial", satisfied=0.4))["outcome"], "incomplete")

    def test_four_distinct_verdicts_from_four_zero_exits(self):
        outcomes = {
            compose(verdict())["outcome"],
            compose(verdict("no_op", satisfied=0.1))["outcome"],
            compose(verdict("blocked_needs_input", awaiting=0.9))["outcome"],
            compose(verdict("completed", confidence=0.5))["outcome"],
        }
        self.assertEqual(len(outcomes), 4)


class TestFailureHandling(unittest.TestCase):
    def test_failure_is_retried(self):
        r = compose(verdict("failed", satisfied=0.05, severity=2.5), rc=1)
        self.assertEqual((r["outcome"], r["next"]), ("failed", "retry"))

    def test_timeout_escalates_and_warns_about_half_applied_work(self):
        r = compose(verdict("partial"), rc=None, timed_out=True)
        self.assertEqual((r["outcome"], r["next"]), ("timeout", "escalate"))
        self.assertTrue(any("half-applied" in n for n in r["notes"]))

    def test_dirty_workspace_is_flagged(self):
        r = compose(verdict("failed", severity=3.4), rc=1)
        self.assertTrue(any("git status" in n for n in r["notes"]))

    def test_needs_human_downgrades_an_otherwise_clean_accept(self):
        r = compose(verdict(needs_human=0.85))
        self.assertEqual(r["next"], "review")


class TestFallbackJudging(unittest.TestCase):
    def test_fallback_confidence_is_capped(self):
        r = compose(verdict(confidence=0.99, source="fallback"))
        self.assertLessEqual(r["signals"]["confidence"], S.THRESHOLDS["FALLBACK_CONFIDENCE_CAP"])

    def test_fallback_never_reaches_accept(self):
        # The cap sits below DONE_MIN_CONFIDENCE, so a degraded judge can
        # report but never wave work through unattended.
        r = compose(verdict(confidence=0.99, satisfied=0.99, source="fallback"))
        self.assertNotEqual(r["next"], "accept")
        self.assertTrue(any("fallback" in n for n in r["notes"]))

    def test_cap_is_below_the_accept_threshold_by_construction(self):
        self.assertLess(S.THRESHOLDS["FALLBACK_CONFIDENCE_CAP"],
                        S.THRESHOLDS["DONE_MIN_CONFIDENCE"])

    def test_terse_success_is_not_mistaken_for_a_no_op(self):
        # Regression: a 39-char successful run was once judged no_op -> retry,
        # which would re-run work that had already succeeded.
        v = S.fallback_judge("patching parser\n2 files changed", 0, False)
        self.assertNotEqual(v["answers"]["status"]["choice"], "no_op")

    def test_fallback_does_not_claim_to_detect_no_ops(self):
        # Telling "did it" from "described it" needs a semantic reader.
        for out in ("", "ok", "x" * 5000, "Here is what I would change: ..."):
            with self.subTest(out=out[:20]):
                v = S.fallback_judge(out, 0, False)
                self.assertNotEqual(v["answers"]["status"]["choice"], "no_op")

    def test_asking_a_question_is_detected(self):
        v = S.fallback_judge("Would you like me to proceed?", 0, False)
        self.assertEqual(v["answers"]["status"]["choice"], "blocked_needs_input")

    def test_traceback_is_detected_even_on_a_zero_exit(self):
        v = S.fallback_judge("Traceback (most recent call last)\nValueError", 0, False)
        self.assertEqual(v["answers"]["status"]["choice"], "failed")

    def test_auth_failure_text_is_treated_as_failure(self):
        for text in ("invalid api key", "not logged in", "quota exceeded"):
            with self.subTest(text=text):
                v = S.fallback_judge(f"error: {text}", 0, False)
                self.assertEqual(v["answers"]["status"]["choice"], "failed")

    def test_nonzero_exit_is_failure(self):
        v = S.fallback_judge("some output", 2, False)
        self.assertEqual(v["answers"]["status"]["choice"], "failed")


class TestExcerpt(unittest.TestCase):
    """The excerpt is what keeps a huge transcript inside Jev's state budget."""

    def test_short_output_passes_through(self):
        self.assertEqual(S.excerpt("hello"), "hello")

    def test_long_output_is_bounded(self):
        out = S.excerpt("x" * 500_000)
        self.assertLess(len(out), S.EXCERPT_HEAD_CHARS + S.EXCERPT_TAIL_CHARS + 200)

    def test_keeps_both_ends(self):
        text = "HEAD_MARKER" + ("m" * 200_000) + "TAIL_MARKER"
        out = S.excerpt(text)
        self.assertIn("HEAD_MARKER", out)
        self.assertIn("TAIL_MARKER", out)  # the tail is where the conclusion lives
        self.assertIn("omitted", out)

    def test_ansi_is_stripped_before_budgeting(self):
        self.assertNotIn("\x1b", S.excerpt("\x1b[31mred\x1b[0m output"))

    def test_bounded_state_fits_jev_budget(self):
        import jev
        state = S.build_state("do a thing", "codex", "y" * 400_000, 0, 12.0, False)
        self.assertLess(len(json.dumps(state)), jev.MAX_STATE_CHARS)


class TestPreconditions(unittest.TestCase):
    def _reg(self, **over):
        card = {"name": "codex", "installed": True, "path": "/usr/bin/codex",
                "headless": {"argv": ["codex", "exec", "{prompt}"]},
                "auth": {"state": "authenticated"}}
        card.update(over)
        return {"agents": [card], "local_inference": {"available": False}}

    def _run(self, reg, name="codex"):
        return S.supervise(name, "task", Path("."), 5, False, 1, reg)

    def test_unknown_agent_errors_without_dispatching(self):
        r = self._run(self._reg(), name="ghost")
        self.assertEqual(r["outcome"], "error")
        self.assertIn("unknown agent", r["notes"][0])

    def test_unauthenticated_agent_is_refused_with_the_fix(self):
        r = self._run(self._reg(auth={"state": "unauthenticated"}))
        self.assertEqual(r["outcome"], "error")
        self.assertIn("codex login", r["notes"][0])

    def test_not_installed_is_refused(self):
        r = self._run(self._reg(installed=False))
        self.assertEqual(r["outcome"], "error")
        self.assertIn("not installed", r["notes"][0])

    def test_missing_headless_contract_is_refused(self):
        r = self._run(self._reg(headless=None))
        self.assertEqual(r["outcome"], "error")
        self.assertIn("headless", r["notes"][0])

    def test_error_verdicts_carry_no_log_path(self):
        self.assertIsNone(self._run(self._reg(), name="ghost")["log"])


class TestWindowsInjectionGuard(unittest.TestCase):
    def test_metacharacters_with_a_cmd_shim_are_refused(self):
        with mock.patch.object(S.os, "name", "nt"):
            self.assertTrue(S.unsafe_on_windows("C:\\x\\gemini.CMD", ['fix & del *']))

    def test_plain_prompt_with_a_cmd_shim_is_fine(self):
        with mock.patch.object(S.os, "name", "nt"):
            self.assertFalse(S.unsafe_on_windows("C:\\x\\gemini.CMD", ["fix the parser"]))

    def test_exe_targets_are_not_affected(self):
        with mock.patch.object(S.os, "name", "nt"):
            self.assertFalse(S.unsafe_on_windows("C:\\x\\codex.EXE", ["fix & del *"]))

    def test_posix_is_never_affected(self):
        with mock.patch.object(S.os, "name", "posix"):
            self.assertFalse(S.unsafe_on_windows("/usr/bin/codex", ["fix & del *"]))


class TestRealDispatch(unittest.TestCase):
    """Actually spawn processes, to prove the Run plumbing works."""

    def _reg(self, code):
        return {"agents": [{
            "name": "fake", "installed": True, "path": sys.executable,
            "status": "installed",
            "headless": {"argv": [sys.executable, "-c", code, "{prompt}"]},
            "auth": {"state": "authenticated"},
        }], "local_inference": {"available": False}}

    def test_output_is_captured_to_a_log_file(self):
        with tempfile.TemporaryDirectory() as td:
            orig, S.RUN_DIR = S.RUN_DIR, Path(td)
            try:
                r = S.supervise("fake", "my task", Path("."), 60, False, 1,
                                self._reg("import sys;print('touched', sys.argv[1])"))
                self.assertIsNotNone(r["log"])
                self.assertIn("touched my task",
                              Path(r["log"]).read_text(encoding="utf-8"))
            finally:
                S.RUN_DIR = orig

    def test_the_prompt_reaches_the_agent_as_one_argv_element(self):
        with tempfile.TemporaryDirectory() as td:
            orig, S.RUN_DIR = S.RUN_DIR, Path(td)
            try:
                r = S.supervise("fake", 'a "quoted" & task', Path("."), 60, False, 1,
                                self._reg("import sys;print(repr(sys.argv[1]))"))
                self.assertIn('a "quoted" & task',
                              Path(r["log"]).read_text(encoding="utf-8"))
            finally:
                S.RUN_DIR = orig

    def test_a_hanging_agent_is_killed_at_the_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            orig, S.RUN_DIR = S.RUN_DIR, Path(td)
            try:
                r = S.supervise("fake", "t", Path("."), 3, False, 1,
                                self._reg("import time;print('start');time.sleep(600)"))
                self.assertEqual(r["outcome"], "timeout")
                self.assertLess(r["elapsed_s"], 30)
            finally:
                S.RUN_DIR = orig


class TestDispatchPlumbing(unittest.TestCase):
    """The supervisor must launch what the planner planned -- and, with no
    metrics to plan from, exactly what it used to launch."""

    CARD = {
        "name": "claude",
        "headless": {
            "argv": ["claude", "-p", "--permission-mode", "acceptEdits",
                     "{flags}", "{prompt}"],
            "modes": {
                "unattended": {"args": ["--permission-mode", "bypassPermissions"],
                               "replaces": ["--permission-mode"], "verified": True},
                "sandbox": {"args": ["-w"], "verified": True},
            },
        },
    }

    def test_no_signals_renders_the_untouched_contract(self):
        cmd = S.render_command(self.CARD, "fix it")
        self.assertEqual(cmd, ["claude", "-p", "--permission-mode", "acceptEdits", "fix it"])

    def test_signals_reach_the_rendered_command(self):
        cmd = S.render_command(self.CARD, "fix it",
                               {"needs_human": 0.05, "blast_radius": 3.0})
        self.assertIn("-w", cmd)
        self.assertIn("bypassPermissions", cmd)
        self.assertNotIn("acceptEdits", cmd)

    def test_route_and_supervise_render_the_same_command(self):
        # "Run the printed command yourself to override" is only safe advice
        # while the printed command is the one that would have run.
        import route
        sig = {"needs_human": 0.05, "blast_radius": 3.0, "task_kind": "refactor"}
        self.assertEqual(route.render_command(self.CARD, "t", sig),
                         S.render_command(self.CARD, "t", sig))


class TestSignalsParsing(unittest.TestCase):
    """--signals has to survive whatever shape the caller pipes into it."""

    def test_reads_a_bare_signals_block(self):
        got = S._load_signals('{"needs_human": 0.1, "task_kind": "refactor"}', None)
        self.assertEqual(got["needs_human"], 0.1)

    def test_reads_route_json_output_whole(self):
        raw = json.dumps({
            "route": {"mode": "auto", "agent": "codex",
                      "signals": {"needs_human": 0.1, "blast_radius": 3.0,
                                  "task_kind": "refactor"}},
            "verdict": {"source": "jev", "answers": {
                "context_breadth": {"score": 3.0}}},
        })
        s = S.dispatch.Signals.from_dict(S._load_signals(raw, None))
        self.assertEqual(s.task_kind, "refactor")
        self.assertEqual(s.blast_radius, 3.0)
        self.assertEqual(s.context_breadth, 3.0)   # picked up from the nested verdict

    def test_nothing_supplied_stays_none(self):
        self.assertIsNone(S._load_signals(None, None))

    def test_a_file_is_read_when_given(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            p.write_text('{"needs_human": 0.2}', encoding="utf-8")
            self.assertEqual(S._load_signals(None, str(p))["needs_human"], 0.2)


class TestRecoveryLoop(unittest.TestCase):
    """A `no_op` is a diagnosis, not a dead end -- but only Jev's diagnosis."""

    ECHO = "import sys;print('ARGS', sys.argv[1:])"

    def _reg(self, recovery=None, modes=None):
        headless = {
            "argv": [sys.executable, "-c", self.ECHO, "{flags}", "{prompt}"],
            "modes": modes if modes is not None else {
                "unattended": {"args": ["--force"], "verified": True}},
        }
        if recovery is not None:
            headless["recovery"] = recovery
        return {"agents": [{
            "name": "fake", "installed": True, "path": sys.executable,
            "status": "installed", "headless": headless,
            "auth": {"state": "authenticated"},
        }], "local_inference": {"available": False}}

    RECOVERY = {
        "no_op": {"prompt_prefix": "APPLY_IT_NOW: ", "modes": ["unattended"],
                  "note": "tell it to actually edit"},
    }

    def _run(self, reg, judgements, **kw):
        """Drive supervise() with a scripted sequence of judge() results."""
        with tempfile.TemporaryDirectory() as td:
            orig, S.RUN_DIR = S.RUN_DIR, Path(td)
            try:
                with mock.patch.object(S, "judge", side_effect=judgements):
                    r = S.supervise("fake", "fix the parser", Path("."), 60,
                                    False, 1, reg, **kw)
                logs = [Path(a["log"]).read_text(encoding="utf-8")
                        for a in r.get("attempts", []) if a.get("log")]
                return r, logs
            finally:
                S.RUN_DIR = orig

    def test_a_no_op_is_retried_with_the_cards_own_cure(self):
        r, logs = self._run(self._reg(recovery=self.RECOVERY),
                            [verdict("no_op", satisfied=0.1), verdict()])
        self.assertEqual(len(r["attempts"]), 2)
        self.assertEqual([a["outcome"] for a in r["attempts"]], ["no_op", "done"])
        self.assertEqual(r["outcome"], "done")
        self.assertNotIn("APPLY_IT_NOW", logs[0])
        self.assertIn("APPLY_IT_NOW", logs[1])      # the cure reached the agent
        self.assertIn("--force", logs[1])           # so did the mode it forced

    def test_a_fallback_verdict_never_triggers_a_retry(self):
        r, _ = self._run(self._reg(recovery=self.RECOVERY),
                         [verdict("no_op", satisfied=0.1, source="fallback"),
                          verdict()])
        self.assertEqual(len(r["attempts"]), 1)
        self.assertTrue(any("fallback" in n for n in r["notes"]))

    def test_the_retry_budget_is_honoured(self):
        r, _ = self._run(self._reg(recovery=self.RECOVERY),
                         [verdict("no_op", satisfied=0.1)], max_retries=0)
        self.assertEqual(len(r["attempts"]), 1)
        self.assertEqual(r["outcome"], "no_op")

    def test_an_agent_with_no_declared_cure_is_not_retried(self):
        r, _ = self._run(self._reg(recovery=None), [verdict("no_op", satisfied=0.1)])
        self.assertEqual(len(r["attempts"]), 1)

    def test_a_clean_run_is_never_retried(self):
        r, _ = self._run(self._reg(recovery=self.RECOVERY), [verdict()])
        self.assertEqual(len(r["attempts"]), 1)
        self.assertEqual(r["outcome"], "done")

    def test_a_cleanup_command_runs_before_the_retry(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "undone.txt"
            rec = {"no_op": {
                "prompt_prefix": "AGAIN: ",
                "cleanup": [sys.executable, "-c",
                            f"open(r'{marker}','w').write('x')"],
            }}
            r, _ = self._run(self._reg(recovery=rec),
                             [verdict("no_op", satisfied=0.1), verdict()])
            self.assertEqual(len(r["attempts"]), 2)
            self.assertTrue(marker.exists(), "the card's cleanup never ran")
            self.assertTrue(any("ran cleanup" in n for n in r["notes"]))

    def test_the_verdict_reports_what_was_injected(self):
        r, _ = self._run(self._reg(), [verdict()], signals={"needs_human": 0.1})
        self.assertEqual(r["dispatch"]["modes"], ["unattended"])

    def test_modes_can_be_switched_off_entirely(self):
        r, logs = self._run(self._reg(), [verdict()],
                            signals={"needs_human": 0.1}, use_modes=False)
        self.assertEqual(r["dispatch"]["modes"], [])
        self.assertNotIn("--force", logs[0])

    def test_retries_share_one_timeout_budget(self):
        # Two attempts must not quietly take twice as long as the caller asked.
        reg = self._reg(recovery=self.RECOVERY)
        reg["agents"][0]["headless"]["argv"] = [
            sys.executable, "-c", "import time;print('x');time.sleep(600)",
            "{flags}", "{prompt}"]
        with tempfile.TemporaryDirectory() as td:
            orig, S.RUN_DIR = S.RUN_DIR, Path(td)
            try:
                started = time.time()
                r = S.supervise("fake", "t", Path("."), 4, False, 1, reg)
                self.assertLess(time.time() - started, 30)
                self.assertEqual(r["outcome"], "timeout")
            finally:
                S.RUN_DIR = orig

    def test_the_judge_grades_the_original_task_not_our_scaffolding(self):
        # Jev must see what the user asked for, not the recovery preamble we
        # wrapped around it -- otherwise it grades our prompt engineering.
        seen = []

        def spy(task, *a, **kw):
            seen.append(task)
            return verdict("no_op", satisfied=0.1) if len(seen) == 1 else verdict()

        with tempfile.TemporaryDirectory() as td:
            orig, S.RUN_DIR = S.RUN_DIR, Path(td)
            try:
                with mock.patch.object(S, "judge", side_effect=spy):
                    S.supervise("fake", "fix the parser", Path("."), 60, False, 1,
                                self._reg(recovery=self.RECOVERY))
            finally:
                S.RUN_DIR = orig
        self.assertEqual(seen, ["fix the parser", "fix the parser"])


class TestVerdictIsSmall(unittest.TestCase):
    """The whole point: the orchestrator reads this, not the transcript."""

    def test_verdict_is_tiny_regardless_of_transcript_size(self):
        r = compose(verdict(), output="noise\n" * 100_000)
        self.assertLess(len(json.dumps(r)), 1500)

    def test_verdict_points_at_the_log_rather_than_inlining_it(self):
        big = "SECRET_BUILD_NOISE\n" * 10_000
        r = compose(verdict(), output=big)
        self.assertNotIn("SECRET_BUILD_NOISE", json.dumps(r)[:-200])
        self.assertTrue(r["log"])

    def test_headline_is_short_and_substantive(self):
        r = compose(verdict(), output="setting up\n=====\nEdited 3 files and ran the suite\n")
        self.assertIn("Edited 3 files", r["headline"])
        self.assertLessEqual(len(r["headline"]), 160)

    def test_headline_skips_separator_lines(self):
        r = compose(verdict(), output="real content here\n--------------------\n")
        self.assertNotIn("----", r["headline"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
