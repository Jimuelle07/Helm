"""Local storage for the user's own TypeSafe (Jev) API key.

This skill is shared across a team, so the key can never be baked into the
skill itself -- each person has their own TypeSafe account. It is entered
once, interactively, and cached locally, the same pattern as `gh auth login`
or a `.netrc` entry: not a secrets vault, just a local file at the same trust
boundary as `~/.netrc` or `~/.aws/credentials`.

Resolution order, highest priority first:

  1. TYPESAFE_API_KEY environment variable   (CI, power users, session overrides)
  2. the stored credential file below         (set once via --set-api-key)
  3. neither -> there is no decision layer, and Helm stops

Point 3 is the whole posture of this module. Jev is not an enhancement that
Helm degrades gracefully without; it is the layer that decides. A missing key
is a configuration error, and `require_api_key()` below turns it into an
immediate, actionable exit rather than a quietly weaker answer.

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


class MissingAPIKey(Exception):
    """No TypeSafe key is configured, so Helm has no decision layer.

    Not a degraded mode and not something a caller should work around: every
    entry point catches this at the top and exits, because a Helm that cannot
    reach Jev cannot route, cannot size blast radius, and cannot judge whether
    a worker finished.
    """


# One wording, used by every entry point. A user who hits this on probe.py and
# again on route.py should be told to do exactly the same thing both times.
SETUP_HINT = "\n".join([
    "No TypeSafe API key configured -- Jev is Helm's decision layer and is required.",
    "  fix: python route.py --set-api-key apikey_...",
    "       (or export TYPESAFE_API_KEY=apikey_... for this session)",
    "  Get a key at https://typesafe.ai -- then verify with: python probe.py --check-jev",
])


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


def require_api_key() -> str:
    """The key, or raise. This is the fail-fast gate the whole skill hangs on.

    Callers use this instead of `get_api_key()` whenever they are about to do
    real work, so the failure lands before an agent is spawned or a task is
    dispatched rather than halfway through one.
    """
    key = get_api_key()
    if not key:
        raise MissingAPIKey(SETUP_HINT)
    return key


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
    the env var always wins, so clearing the file alone will not disable Jev
    in a shell that also exports the variable.
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
        help="store your required TypeSafe API key locally "
             "(~/.cache/helm/credentials.json) and exit",
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
        print("Helm's decision layer is configured. "
              "Confirm it works with: python probe.py --check-jev")
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
                "still be used -- unset it too if you meant to remove the key entirely."
            )
        elif removed:
            print(
                "Helm now has no decision layer: route.py and supervise.py will "
                "exit until a key is set again."
            )
        return 0
    return None
