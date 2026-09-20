"""Tests for the auth faultline.

The rule this file exists to protect is asymmetric, and getting it backwards
breaks the tool in one of two ways:

  * gate on a missing credential  -> working agents silently vanish from the
                                     answer space (a false negative we have
                                     already observed with cursor-agent)
  * ignore a confirmed logout     -> we route to an agent that cannot call a
                                     model, and the user sees a confusing
                                     runtime failure instead of "go log in"

Only the tool's own status command may condemn an agent. Everything else is
"unknown", and unknown never blocks.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import probe  # noqa: E402

NO_LOCAL = {"available": False, "vram_gb": 6.0}


def agent(name="codex", *, state="unknown", verified=False, installed=True,
          headless=True, ready=True, detail="", status="installed"):
    return {
        "name": name, "installed": installed, "status": status,
        "path": f"/usr/bin/{name}", "headless": {"argv": [name, "-p", "{prompt}"]} if headless else None,
        "requires": {},
        "auth": {"ready": ready, "state": state, "detail": detail, "verified": verified},
    }


class TestFaultline(unittest.TestCase):
    def test_confirmed_logout_disqualifies(self):
        ok, why = probe.routable(
            agent(state="unauthenticated", verified=True, detail="Not logged in"), NO_LOCAL)
        self.assertFalse(ok)
        self.assertIn("not authenticated", why)

    def test_the_reason_carries_the_detail_so_the_user_can_act(self):
        _, why = probe.routable(
            agent(state="unauthenticated", verified=True,
                  detail="Not logged in. Run `codex login`."), NO_LOCAL)
        self.assertIn("codex login", why)

    def test_unknown_auth_never_disqualifies(self):
        # The cursor-agent case: heuristic found nothing, tool was in fact
        # logged in. Unknown must stay routable.
        ok, _ = probe.routable(agent(state="unknown", verified=False, ready=False), NO_LOCAL)
        self.assertTrue(ok)

    def test_verified_authenticated_is_routable(self):
        ok, _ = probe.routable(agent(state="authenticated", verified=True), NO_LOCAL)
        self.assertTrue(ok)

    def test_heuristic_alone_cannot_block(self):
        # ready=False is the old heuristic saying "no credential found".
        # On its own that must not change routability.
        for state in ("unknown", "authenticated"):
            with self.subTest(state=state):
                ok, _ = probe.routable(agent(state=state, ready=False), NO_LOCAL)
                self.assertTrue(ok)

    def test_every_login_hint_names_a_real_remedy(self):
        for name, hint in probe.LOGIN_HINTS.items():
            with self.subTest(agent=name):
                self.assertTrue(hint.strip())
                self.assertGreater(len(hint), 8)


class TestVerifyAuth(unittest.TestCase):
    """verify_auth() turns a status command's output into one of three states."""

    def _card(self, **check):
        base = {"argv": ["tool", "status"], "timeout": 5}
        base.update(check)
        return {"name": "tool", "auth_check": base}

    def _run(self, rc, out, **check):
        with mock.patch.object(probe, "run", return_value=(rc, out)):
            return probe.verify_auth(self._card(**check), "/usr/bin/tool")

    def test_ok_pattern_match_is_authenticated(self):
        r = self._run(0, "Logged in as someone", ok_pattern="logged in as")
        self.assertEqual(r["state"], "authenticated")
        self.assertTrue(r["verified"])

    def test_fail_pattern_beats_a_zero_exit(self):
        # A CLI can exit 0 while printing "not logged in"; the explicit
        # negative has to win over the return code.
        r = self._run(0, "You are not logged in.",
                      ok_pattern="logged in", fail_pattern="not logged in")
        self.assertEqual(r["state"], "unauthenticated")

    def test_unrecognised_output_is_unknown_not_authenticated(self):
        # A CLI version bump changes the wording. Guessing "fine" here is how
        # you route to a dead agent, so ambiguity must resolve to unknown.
        r = self._run(0, "Session: active (v2 format)", ok_pattern="logged in as")
        self.assertEqual(r["state"], "unknown")
        self.assertFalse(r["verified"])

    def test_timeout_is_unknown_not_a_condemnation(self):
        r = self._run(124, "TimeoutExpired", ok_pattern="logged in")
        self.assertEqual(r["state"], "unknown")

    def test_no_auth_check_on_card_is_unknown(self):
        r = probe.verify_auth({"name": "gemini"}, "/usr/bin/gemini")
        self.assertEqual(r["state"], "unknown")
        self.assertFalse(r["verified"])

    def test_nonzero_exit_without_patterns_is_unauthenticated(self):
        r = self._run(1, "error: no session")
        self.assertEqual(r["state"], "unauthenticated")

    def test_zero_exit_without_patterns_is_authenticated(self):
        r = self._run(0, "all good")
        self.assertEqual(r["state"], "authenticated")

    def test_email_is_redacted_from_the_detail(self):
        # The registry gets written to disk and pasted into bug reports.
        r = self._run(0, "Logged in as someone@example.com", ok_pattern="logged in as")
        self.assertNotIn("someone@example.com", r["detail"])
        self.assertIn("<redacted>", r["detail"])

    def test_ansi_codes_do_not_defeat_pattern_matching(self):
        r = self._run(0, "\x1b[32m✓\x1b[39m Logged in as x", ok_pattern="logged in as")
        self.assertEqual(r["state"], "authenticated")

    def test_resolved_path_is_used_not_the_bare_name(self):
        seen = {}

        def fake_run(argv, timeout=10):
            seen["argv"] = argv
            return 0, "Logged in"
        with mock.patch.object(probe, "run", fake_run):
            probe.verify_auth(self._card(ok_pattern="logged in"), "/opt/real/tool")
        self.assertEqual(seen["argv"][0], "/opt/real/tool")


class TestOpencodeCredentialCount(unittest.TestCase):
    """`opencode auth list` exits 0 even with nothing configured, so the count
    is the real signal. Guard the regexes that encode that."""

    def _check(self):
        import json
        card = json.loads((SCRIPTS / "cards" / "opencode.json").read_text(encoding="utf-8"))
        return card["auth_check"]

    def _state(self, output):
        with mock.patch.object(probe, "run", return_value=(0, output)):
            return probe.verify_auth({"name": "opencode", "auth_check": self._check()},
                                     "/usr/bin/opencode")["state"]

    def test_some_credentials_is_authenticated(self):
        self.assertEqual(self._state("3 credentials"), "authenticated")

    def test_one_credential_singular_is_authenticated(self):
        self.assertEqual(self._state("1 credential"), "authenticated")

    def test_zero_credentials_is_unauthenticated(self):
        self.assertEqual(self._state("0 credentials"), "unauthenticated")


class TestVerifyAllAuthParallel(unittest.TestCase):
    def test_merges_results_and_survives_a_broken_check(self):
        agents = [
            {**agent("good"), "auth": dict(agent("good")["auth"])},
            {**agent("bad"), "auth": dict(agent("bad")["auth"])},
        ]
        cards = {"good": {"name": "good", "auth_check": {"argv": ["good", "s"]}},
                 "bad": {"name": "bad", "auth_check": {"argv": ["bad", "s"]}}}

        def flaky(card, resolved):
            if card["name"] == "bad":
                raise RuntimeError("boom")
            return {"state": "authenticated", "detail": "ok", "verified": True}

        with mock.patch.object(probe, "verify_auth", flaky):
            probe.verify_all_auth(agents, cards)

        by = {a["name"]: a for a in agents}
        self.assertEqual(by["good"]["auth"]["state"], "authenticated")
        # A check that raises must not sink the probe, and must not condemn.
        self.assertEqual(by["bad"]["auth"]["state"], "unknown")

    def test_only_restricts_the_sweep(self):
        agents = [{**agent(n), "auth": dict(agent(n)["auth"])} for n in ("a", "b")]
        cards = {n: {"name": n, "auth_check": {"argv": [n, "s"]}} for n in ("a", "b")}
        called = []

        def spy(card, resolved):
            called.append(card["name"])
            return {"state": "authenticated", "detail": "", "verified": True}

        with mock.patch.object(probe, "verify_auth", spy):
            probe.verify_all_auth(agents, cards, only={"a"})
        self.assertEqual(called, ["a"])

    def test_agents_without_a_check_are_skipped(self):
        agents = [{**agent("gemini"), "auth": dict(agent("gemini")["auth"])}]
        with mock.patch.object(probe, "verify_auth") as m:
            probe.verify_all_auth(agents, {"gemini": {"name": "gemini"}})
        m.assert_not_called()


class TestRecheckAfterLogin(unittest.TestCase):
    """Telling a user "log in" and then still reporting them logged out is the
    worst possible staleness, because it is the exact loop we asked them to close."""

    def _registry(self, state):
        return {
            "agents": [{**agent("codex"), "auth": {"ready": True, "state": state,
                                                   "detail": "", "verified": True},
                        "routable": state != "unauthenticated", "routable_reason": ""}],
            "local_inference": NO_LOCAL,
        }

    def test_logged_out_agent_is_rechecked(self):
        reg = self._registry("unauthenticated")
        with mock.patch.object(probe, "verify_all_auth") as m, \
             mock.patch.object(probe, "load_cards", return_value=[{"name": "codex"}]):
            probe._recheck_failed_auth(reg)
        m.assert_called_once()
        self.assertEqual(m.call_args.kwargs["only"], {"codex"})

    def test_healthy_cache_costs_nothing(self):
        reg = self._registry("authenticated")
        with mock.patch.object(probe, "verify_all_auth") as m:
            probe._recheck_failed_auth(reg)
        m.assert_not_called()

    def test_recheck_restores_routability_after_a_successful_login(self):
        reg = self._registry("unauthenticated")

        def relogin(agents, cards, only=None):
            for a in agents:
                a["auth"]["state"] = "authenticated"

        with mock.patch.object(probe, "verify_all_auth", relogin), \
             mock.patch.object(probe, "load_cards", return_value=[{"name": "codex"}]):
            probe._recheck_failed_auth(reg)
        self.assertTrue(reg["agents"][0]["routable"])


class TestStripAnsi(unittest.TestCase):
    def test_removes_colour_codes(self):
        self.assertEqual(probe.strip_ansi("\x1b[34m●\x1b[39m OpenRouter"), "● OpenRouter")

    def test_leaves_plain_text_alone(self):
        self.assertEqual(probe.strip_ansi("plain text"), "plain text")

    def test_handles_osc_sequences(self):
        self.assertEqual(probe.strip_ansi("\x1b]0;title\x07done"), "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
