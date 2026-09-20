"""Tests for local TypeSafe API key storage.

This is the piece that makes the skill usable by more than one person: each
user configures their own key, so it must round-trip correctly, never leak
into logs, and always let an environment variable override the stored value.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "agent-router" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import keystore  # noqa: E402


class KeystoreTestCase(unittest.TestCase):
    """Points AGENT_ROUTER_CREDENTIALS at a throwaway file for every test, so
    a real key on the machine running these tests is never touched."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._path = Path(self._tmpdir.name) / "credentials.json"
        self._orig_path = keystore.CREDENTIALS_PATH
        self._orig_env = os.environ.pop("TYPESAFE_API_KEY", None)
        keystore.CREDENTIALS_PATH = self._path

    def tearDown(self):
        keystore.CREDENTIALS_PATH = self._orig_path
        self._tmpdir.cleanup()
        if self._orig_env is not None:
            os.environ["TYPESAFE_API_KEY"] = self._orig_env
        else:
            os.environ.pop("TYPESAFE_API_KEY", None)


class TestRoundTrip(KeystoreTestCase):
    def test_no_key_anywhere(self):
        self.assertIsNone(keystore.get_api_key())
        self.assertIsNone(keystore.key_source())

    def test_set_then_get(self):
        keystore.set_api_key("sk-abc123")
        self.assertEqual(keystore.get_api_key(), "sk-abc123")
        self.assertEqual(keystore.key_source(), "stored")

    def test_set_strips_whitespace(self):
        keystore.set_api_key("  sk-abc123  \n")
        self.assertEqual(keystore.get_api_key(), "sk-abc123")

    def test_set_rejects_empty(self):
        with self.assertRaises(ValueError):
            keystore.set_api_key("   ")

    def test_overwrite_replaces_previous_key(self):
        keystore.set_api_key("sk-first")
        keystore.set_api_key("sk-second")
        self.assertEqual(keystore.get_api_key(), "sk-second")

    def test_clear_removes_stored_key(self):
        keystore.set_api_key("sk-abc123")
        self.assertTrue(keystore.clear_api_key())
        self.assertIsNone(keystore.get_api_key())

    def test_clear_when_nothing_stored_returns_false(self):
        self.assertFalse(keystore.clear_api_key())

    def test_clear_removes_the_file_when_it_becomes_empty(self):
        keystore.set_api_key("sk-abc123")
        keystore.clear_api_key()
        self.assertFalse(keystore.CREDENTIALS_PATH.exists())

    def test_persists_across_a_fresh_read(self):
        # set_api_key/get_api_key don't cache in memory -- confirm a second,
        # independent read sees what a first call wrote.
        keystore.set_api_key("sk-abc123")
        self.assertEqual(keystore._read()["typesafe_api_key"], "sk-abc123")


class TestPrecedence(KeystoreTestCase):
    def test_env_var_wins_over_stored(self):
        keystore.set_api_key("sk-stored")
        os.environ["TYPESAFE_API_KEY"] = "sk-env"
        self.assertEqual(keystore.get_api_key(), "sk-env")
        self.assertEqual(keystore.key_source(), "env")

    def test_falls_back_to_stored_when_env_unset(self):
        keystore.set_api_key("sk-stored")
        self.assertEqual(keystore.get_api_key(), "sk-stored")

    def test_clearing_stored_key_does_not_touch_env(self):
        os.environ["TYPESAFE_API_KEY"] = "sk-env"
        keystore.set_api_key("sk-stored")
        keystore.clear_api_key()
        self.assertEqual(keystore.get_api_key(), "sk-env")


class TestMasking(unittest.TestCase):
    def test_long_key_shows_only_edges(self):
        masked = keystore.mask("sk-abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz"[3:-3], masked)
        self.assertTrue(masked.startswith("sk-ab"))
        self.assertTrue(masked.endswith("wxyz"))

    def test_short_key_is_fully_masked(self):
        self.assertEqual(keystore.mask("sk-1"), "****")

    def test_mask_never_returns_the_input_unchanged(self):
        for key in ("sk-abc123", "short", "x" * 40):
            with self.subTest(key=key):
                self.assertNotEqual(keystore.mask(key), key)


class TestCliIntegration(KeystoreTestCase):
    """The --set-api-key / --clear-api-key flags shared by probe.py and route.py."""

    def _args(self, set_api_key=None, clear_api_key=False):
        ns = type("Args", (), {})()
        ns.set_api_key = set_api_key
        ns.clear_api_key = clear_api_key
        return ns

    def test_neither_flag_returns_none(self):
        self.assertIsNone(keystore.handle_key_args(self._args()))

    def test_set_flag_stores_and_returns_zero(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = keystore.handle_key_args(self._args(set_api_key="sk-fromcli"))
        self.assertEqual(rc, 0)
        self.assertEqual(keystore.get_api_key(), "sk-fromcli")

    def test_set_flag_never_prints_the_full_key(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            keystore.handle_key_args(self._args(set_api_key="sk-supersecretvalue"))
        self.assertNotIn("sk-supersecretvalue", buf.getvalue())

    def test_clear_flag_removes_and_returns_zero(self):
        keystore.set_api_key("sk-abc123")
        rc = keystore.handle_key_args(self._args(clear_api_key=True))
        self.assertEqual(rc, 0)
        self.assertIsNone(keystore.get_api_key())

    def test_clear_flag_warns_if_env_var_still_set(self):
        os.environ["TYPESAFE_API_KEY"] = "sk-env"
        buf = io.StringIO()
        with redirect_stdout(buf):
            keystore.handle_key_args(self._args(clear_api_key=True))
        self.assertIn("TYPESAFE_API_KEY", buf.getvalue())


class TestFilePermissions(KeystoreTestCase):
    @unittest.skipIf(os.name == "nt", "POSIX file mode bits don't apply on Windows")
    def test_credentials_file_is_owner_only(self):
        import stat
        keystore.set_api_key("sk-abc123")
        mode = stat.S_IMODE(keystore.CREDENTIALS_PATH.stat().st_mode)
        self.assertEqual(mode, 0o600)

class TestDestructiveFlagIsNotAbbreviable(KeystoreTestCase):
    """Regression: argparse abbreviates long options by default, which made
    `--clear-api-key` reachable as `--clear` -- one typo away from silently
    deleting a user's credentials. Every parser that exposes the flag must
    disable abbreviation."""

    def _parser_sources(self):
        import re
        for name in ("probe.py", "route.py", "supervise.py"):
            yield name, (SCRIPTS / name).read_text(encoding="utf-8")

    def test_every_cli_disables_argparse_abbreviation(self):
        for name, src in self._parser_sources():
            with self.subTest(script=name):
                self.assertIn("allow_abbrev=False", src,
                              f"{name} must pass allow_abbrev=False to ArgumentParser")

    def test_abbreviated_clear_flag_is_rejected(self):
        import argparse
        ap = argparse.ArgumentParser(allow_abbrev=False)
        keystore.add_key_args(ap)
        with self.assertRaises(SystemExit):
            ap.parse_args(["--clear"])

    def test_exact_clear_flag_still_parses(self):
        import argparse
        ap = argparse.ArgumentParser(allow_abbrev=False)
        keystore.add_key_args(ap)
        self.assertTrue(ap.parse_args(["--clear-api-key"]).clear_api_key)

if __name__ == "__main__":
    unittest.main(verbosity=2)
