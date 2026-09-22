"""Tests for Jev-judged completion.

The property worth protecting here: an exit code cannot tell "wrote the code"
from "wrote about the code". Several of these cases exit 0 and must still
produce different verdicts, because that distinction is the entire reason a
semantic judge is worth a network round-trip.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import supervise as S  # noqa: E402

LOG = Path("run.log")


def verdict(status="completed", *, confidence=0.9, satisfied=0.9, needs_human=0.1,
            awaiting=0.02, severity=0.3):
    return {
        "source": "jev",
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


class TestJevIsTheOnlyJudge(unittest.TestCase):
    """"Did the agent do the work?" has exactly one answerer."""

    def test_the_heuristic_judge_is_gone(self):
        for name in ("fallback_judge", "ASKING_MARKERS", "FAILURE_MARKERS"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(S, name),
                                 f"supervise.{name} should not exist any more")

    def test_no_confidence_cap_threshold_remains(self):
        self.assertNotIn("FALLBACK_CONFIDENCE_CAP", S.THRESHOLDS)

    def test_a_confident_verdict_is_not_demoted(self):
        r = compose(verdict(confidence=0.99, satisfied=0.99))
        self.assertEqual(r["next"], "accept")
        self.assertAlmostEqual(r["signals"]["confidence"], 0.99, places=3)
        self.assertEqual(r["judged_by"], "jev")

    def test_judge_propagates_an_outage_instead_of_answering_anyway(self):
        # The old code caught this and returned a keyword-scan verdict. The
        # whole point of the refactor is that it no longer can.
        with mock.patch.object(S.jev, "ask",
                               side_effect=S.jev.JevUnavailable("network down")):
            with self.assertRaises(S.jev.JevUnavailable):
                S.judge("t", "codex", "some output", 0, 1.0, False)


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


class TestWindowsNewlineCollapse(unittest.TestCase):
    """A multi-line prompt re-parsed by cmd.exe through a .cmd/.bat shim gets
    truncated at the first line break -- the agent receives a partial task,
    and Jev correctly reports that partial task as a no_op or a stuck run.
    Collapsing newlines before the shim ever sees them is the fix."""

    def test_a_multiline_prompt_is_collapsed_for_a_cmd_shim(self):
        with mock.patch.object(S.os, "name", "nt"):
            argv = S.collapse_newlines_for_windows_shim(
                "C:\\x\\opencode.cmd", ["opencode", "run", "line one\nline two"])
        self.assertEqual(argv, ["opencode", "run", "line one line two"])

    def test_crlf_is_also_collapsed(self):
        with mock.patch.object(S.os, "name", "nt"):
            argv = S.collapse_newlines_for_windows_shim(
                "C:\\x\\opencode.CMD", ["opencode", "a\r\nb"])
        self.assertEqual(argv, ["opencode", "a b"])

    def test_a_single_line_prompt_is_left_untouched(self):
        with mock.patch.object(S.os, "name", "nt"):
            argv = S.collapse_newlines_for_windows_shim(
                "C:\\x\\opencode.cmd", ["opencode", "fix the parser"])
        self.assertEqual(argv, ["opencode", "fix the parser"])

    def test_exe_targets_are_not_affected(self):
        with mock.patch.object(S.os, "name", "nt"):
            argv = S.collapse_newlines_for_windows_shim(
                "C:\\x\\codex.EXE", ["codex", "line one\nline two"])
        self.assertEqual(argv, ["codex", "line one\nline two"])

    def test_posix_is_never_affected(self):
        with mock.patch.object(S.os, "name", "posix"):
            argv = S.collapse_newlines_for_windows_shim(
                "/usr/bin/opencode", ["opencode", "line one\nline two"])
        self.assertEqual(argv, ["opencode", "line one\nline two"])

    def test_no_resolved_path_is_a_no_op(self):
        argv = S.collapse_newlines_for_windows_shim(None, ["opencode", "a\nb"])
        self.assertEqual(argv, ["opencode", "a\nb"])


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

    def setUp(self):
        # supervise() now refuses to dispatch without a key, so these tests
        # supply one explicitly rather than depending on the developer's
        # machine happening to have one configured.
        self._old_key = os.environ.get("TYPESAFE_API_KEY")
        os.environ["TYPESAFE_API_KEY"] = "apikey_test_notreal"

    def tearDown(self):
        if self._old_key is None:
            os.environ.pop("TYPESAFE_API_KEY", None)
        else:
            os.environ["TYPESAFE_API_KEY"] = self._old_key

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

    def test_an_unjudged_run_never_triggers_a_retry(self):
        # Jev dies after the agent has already run. We do not know what it did
        # to the workspace, which is the worst possible basis for doing it
        # again -- so the run escalates with its transcript instead.
        r, _ = self._run(self._reg(recovery=self.RECOVERY),
                         [S.jev.JevUnavailable("network down"), verdict()])
        self.assertEqual(r["outcome"], "error")
        self.assertEqual(r["next"], "escalate")
        self.assertIsNotNone(r["log"], "the transcript must still be reachable")
        self.assertTrue(any("could not judge" in n for n in r["notes"]))

    def test_supervise_refuses_to_dispatch_without_a_key(self):
        # Checked before anything is spawned: an unjudgeable run is not worth
        # the user's tokens or the risk to their working tree.
        import keystore
        old_path = keystore.CREDENTIALS_PATH
        os.environ.pop("TYPESAFE_API_KEY", None)
        with tempfile.TemporaryDirectory() as td:
            keystore.CREDENTIALS_PATH = Path(td) / "credentials.json"
            try:
                with mock.patch.object(S, "judge") as judged:
                    r = S.supervise("fake", "fix the parser", Path("."), 60,
                                    False, 1, self._reg())
            finally:
                keystore.CREDENTIALS_PATH = old_path

        self.assertEqual(r["outcome"], "error")
        self.assertIn("--set-api-key", r["notes"][0])
        judged.assert_not_called()
        self.assertNotIn("attempts", r)

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
                # judge() is stubbed because this test is about the budget, not
                # the verdict -- and a real judge() would now reach for Jev.
                # `timed_out` is what drives the outcome here regardless.
                with mock.patch.object(S, "judge", return_value=verdict()):
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
