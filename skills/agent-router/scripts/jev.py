#!/usr/bin/env python3
"""Shared TypeSafe Jev client.

Jev is a System One model: it emits no tokens, only typed decisions. You hand
it state plus typed questions and it answers them in parallel in one forward
pass -- 70-500ms, $0.042 per million input tokens, output free.

Both callers in this skill use it through here:
  * route.py     -- which agent should take this task?
  * supervise.py -- did the agent it dispatched actually finish?

Keeping the client in one module means the auth path, the model pin, the
timeout and the degradation behaviour are defined once. Every failure mode
raises JevUnavailable, which callers are expected to catch and answer with a
deterministic fallback -- Jev being unreachable must degrade the judgement,
never break the tool.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import keystore  # noqa: E402

ENDPOINT = os.environ.get("TYPESAFE_ENDPOINT", "https://api.typesafe.ai/v1/systemone")
# Pinned, not `jev-latest`: thresholds calibrated against one model version are
# silently invalidated by an alias shift. The pin lives next to what it protects.
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-1.13.0")
TIMEOUT = 15

# Jev allows 64k per request, 32k for state plus the longest question. We stay
# well inside that: state is always a summary, never raw source or a full
# transcript. Callers that hold large text must excerpt it first.
MAX_STATE_CHARS = 90_000


class JevUnavailable(Exception):
    """Raised whenever Jev cannot answer, for any reason. Always caught."""


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, criteria: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


def available() -> bool:
    return keystore.get_api_key() is not None


def _encode_question(q: dict) -> dict:
    """Wire encoding for one question on the raw-HTTP path.

    NOTE: inferred from the documented SDK surface (Choice/Score/Noul with
    `instructions` plus `criteria`), not verified against a live endpoint --
    this project has no configured API key to test against. If the raw-HTTP
    path 400s, check the current schema at docs.typesafe.ai and fix it here;
    the SDK path is authoritative and unaffected. Any failure falls through to
    the caller's deterministic fallback, so a wrong guess degrades the answer
    rather than breaking the tool.
    """
    return {k: v for k, v in q.items() if v is not None}


def ask(state: dict, questions: dict) -> dict:
    """One request, many questions, evaluated independently and in parallel."""
    api_key = keystore.get_api_key()
    if not api_key:
        raise JevUnavailable(
            "no TypeSafe API key configured. Run "
            "`python route.py --set-api-key sk-...` once, or set TYPESAFE_API_KEY."
        )

    blob = json.dumps(state)
    if len(blob) > MAX_STATE_CHARS:
        raise JevUnavailable(
            f"state is {len(blob)} chars, over the {MAX_STATE_CHARS} budget -- "
            "excerpt it before calling (see supervise.excerpt)"
        )

    # Prefer the official SDK when it is installed: it owns the wire format.
    try:
        from typesafe_sdk import Choice, Noul, Score, TypeSafeClient  # type: ignore

        built = {}
        for key, q in questions.items():
            if q["type"] == "choice":
                built[key] = Choice(instructions=q["instructions"], criteria=q["criteria"])
            elif q["type"] == "score":
                built[key] = Score(instructions=q["instructions"], criteria=q["criteria"])
            else:
                built[key] = Noul(instructions=q["instructions"])
        # The documented SDK usage constructs TypeSafeClient() with no arguments,
        # implying it reads TYPESAFE_API_KEY from the environment. A key entered
        # via --set-api-key lives only in our local credential file, so export it
        # for this process before the client reads it. The process exits shortly
        # after either way, so nothing leaks back to the invoking shell.
        os.environ.setdefault("TYPESAFE_API_KEY", api_key)
        client = TypeSafeClient()
        resp = client.system_one(state=state, questions=built, model=MODEL)
        return json.loads(resp.model_dump_json()) if hasattr(resp, "model_dump_json") else dict(resp)
    except ImportError:
        pass
    except Exception as exc:  # SDK present but the call failed
        raise JevUnavailable(f"SDK call failed: {exc}") from exc

    payload = json.dumps({
        "model": MODEL,
        "state": state,
        "questions": {k: _encode_question(q) for k, q in questions.items()},
    }).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise JevUnavailable(f"HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise JevUnavailable(str(exc)) from exc


def answer(resp: dict, key: str, field: str, default: float = 0.0) -> float:
    """Pull one numeric field out of a response, tolerating a missing answer."""
    got = (resp.get("answers") or {}).get(key) or {}
    val = got.get(field)
    return float(val) if isinstance(val, (int, float)) else default


def answer_choice(resp: dict, key: str, default: str = "") -> str:
    got = (resp.get("answers") or {}).get(key) or {}
    return got.get("choice") or default
