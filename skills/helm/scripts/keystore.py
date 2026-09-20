"""Local storage for the user's own TypeSafe (Jev) API key.

This skill is shared across a team, so the key can never be baked into the
skill itself -- each person has their own TypeSafe account. It is entered
once, interactively, and cached locally, the same pattern as `gh auth login`
or a `.netrc` entry: not a secrets vault, just a local file at the same trust
boundary as `~/.netrc` or `~/.aws/credentials`.

Resolution order, highest priority first:

  1. TYPESAFE_API_KEY environment variable   (CI, power users, session overrides)
  2. the stored credential file below         (set once via --set-api-key)
  3. neither -> Jev is unavailable; callers fall back to the deterministic scorer

Both probe.py and route.py import this module so "is Jev configured?" and
"what key do I call Jev with?" always agree.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

CREDENTIALS_PATH = Path(
    os.environ.get("HELM_CREDENTIALS")
    or Path.home() / ".cache" / "helm" / "credentials.json"
)


def _read() -> dict:
    try:
        return json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(data), encoding="utf-8")
    try:
        # Best-effort on POSIX; Windows has no stdlib equivalent for a
        # single-user-readable file, so the file itself must be treated as
        # sensitive there (it lives under the user's own profile either way).
        os.chmod(CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def get_api_key() -> str | None:
    """The key `route.py` should actually call Jev with, or None."""
    env = os.environ.get("TYPESAFE_API_KEY")
    if env:
        return env
    stored = _read().get("typesafe_api_key")
    return stored or None


def key_source() -> str | None:
    """Where the active key came from, for status reporting only.

    Never returns the key itself -- callers that need to *display* something
    should use mask() on the value from get_api_key().
    """
    if os.environ.get("TYPESAFE_API_KEY"):
        return "env"
    if _read().get("typesafe_api_key"):
        return "stored"
    return None


def set_api_key(key: str) -> None:
    key = key.strip()
    if not key:
        raise ValueError("empty API key")
    data = _read()
    data["typesafe_api_key"] = key
    _write(data)


def clear_api_key() -> bool:
    """Remove the stored key. Returns False if nothing was stored.

    Has no effect on TYPESAFE_API_KEY if that's set in the environment --
    the env var always wins, so clearing the file alone will not make Jev
    unavailable in a shell that also exports the variable.
    """
    data = _read()
    if "typesafe_api_key" not in data:
        return False
    del data["typesafe_api_key"]
    if data:
        _write(data)
    else:
        try:
            CREDENTIALS_PATH.unlink()
        except OSError:
            pass
    return True


def mask(key: str) -> str:
    """For confirmation messages. Never print a key in full."""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:5]}...{key[-4:]}"


def add_key_args(parser) -> None:
    """Shared --set-api-key / --clear-api-key flags for probe.py and route.py."""
    parser.add_argument(
        "--set-api-key", metavar="KEY",
        help="store your TypeSafe API key locally (~/.cache/helm/credentials.json) and exit",
    )
    parser.add_argument(
        "--clear-api-key", action="store_true",
        help="remove any locally stored TypeSafe API key and exit",
    )


def handle_key_args(args) -> int | None:
    """Handle --set-api-key / --clear-api-key if present. Returns an exit code
    to return immediately, or None if neither flag was given and the caller
    should continue with its normal work."""
    if getattr(args, "set_api_key", None):
        key = args.set_api_key
        # Observed live format is `apikey_<id>_<secret>`. `sk-` is accepted too
        # because some docs still describe that shape. Anything else is stored
        # anyway with a nudge -- refusing an unfamiliar prefix would be a
        # guaranteed future bug the first time TypeSafe changes it.
        if not key.startswith(("apikey_", "sk-")):
            print("note: that does not look like a TypeSafe key "
                  "(expected 'apikey_...'), but storing it anyway.")
        set_api_key(key)
        print(f"Stored TypeSafe API key ({mask(key)}) at {CREDENTIALS_PATH}")
        print("Jev is now available. Run probe.py to confirm.")
        return 0
    if getattr(args, "clear_api_key", False):
        removed = clear_api_key()
        if removed:
            print(f"Removed the stored key at {CREDENTIALS_PATH}")
        else:
            print("No stored key to remove.")
        if os.environ.get("TYPESAFE_API_KEY"):
            print(
                "Note: TYPESAFE_API_KEY is still set in this environment and will "
                "still be used -- unset it if you want Jev fully disabled."
            )
        return 0
    return None
