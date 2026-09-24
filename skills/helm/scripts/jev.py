#!/usr/bin/env python3
"""Shared TypeSafe Jev client.

Jev is a System One model: it emits no tokens, only typed decisions. You hand
it state plus typed questions and it answers them in parallel in one forward
pass -- 70-500ms, $0.042 per million input tokens, output free.

Both callers in this skill use it through here:
  * route.py     -- which agent should take this task?
  * supervise.py -- did the agent it dispatched actually finish?

Keeping the client in one module means the auth path, the model pin and the
timeout are defined once. Every failure mode raises JevUnavailable, and there
is no second opinion to fall back to: Jev is the decision layer, so a Jev that
cannot answer means Helm has no answer. Callers surface the failure and stop
rather than substituting a weaker judgement the user did not ask for.
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
MODELS_ENDPOINT = os.environ.get(
    "TYPESAFE_MODELS_ENDPOINT", "https://api.typesafe.ai/v1/models")

# Pinned, not `jev-latest`: thresholds calibrated against one model version are
# silently invalidated by an alias shift. The pin lives next to what it protects.
#
# Confirmed on 2026-09-20 that this is a real pin and not decoration: the API
# validates it (`jev-9.99.9` -> 400 "Unknown model"), and every response echoes
# the version it actually ran. Note that GET /v1/models lists only the aliases
# `jev-latest` and `jev-preview` -- concrete versions are accepted but not
# enumerated, so this string cannot be discovered from the API and has to be
# read off a response (or the changelog) when it is time to move it.
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-1.13.0")
TIMEOUT = 15

# Jev allows 64k per request, 32k for state plus the longest question. We stay
# well inside that: state is always a summary, never raw source or a full
# transcript. Callers that hold large text must excerpt it first.
MAX_STATE_CHARS = 90_000


class JevUnavailable(Exception):
    """Raised whenever Jev cannot answer, for any reason.

    This is a terminal condition, not a branch point. Nothing in this skill
    catches it and continues with a locally computed answer -- callers report
    it and exit, because a routing or completion verdict that did not come
    from Jev is not a Helm verdict at all.
    """


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, criteria: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


def available() -> bool:
    """Whether a key is configured. Status reporting only.

    Work paths call `require()` instead: this returns a bool that invites an
    `if` around the decision layer, which is exactly the branch this skill no
    longer has.
    """
    return keystore.get_api_key() is not None


def require() -> str:
    """Fail fast unless Jev is configured. Raises keystore.MissingAPIKey."""
    return keystore.require_api_key()


def _encode_question(q: dict) -> dict:
    """Wire encoding for one question on the raw-HTTP path.

    VERIFIED against the live endpoint on 2026-09-20 with jev-1.13.0. The
    observed contract:

      POST /v1/systemone
        Authorization: Bearer <key>          (x-api-key is rejected, 403)
        {"model": "...",                      (required -- omitting it 422s)
         "state": <object>,
         "questions": {"<key>": {"type": "choice"|"score"|"noul",
                                 "instructions": "...",
                                 "criteria": {...} | [...]}}}

      -> {"model": "jev-1.13.0",              (the RESOLVED version, see below)
          "answers": {"<key>": {"type": ..., "choice"/"score"/"noul": ...,
                                "confidence": float,     (choice & score only)
                                "probabilities": {...},  (choice: by option name;
                                                          score: by index string)
                                "legend": {...}}},       (score only: index -> label)
          "usage": {"input_tokens": int, "output_tokens": int}}

    A `score` answer keys its probabilities by stringified index ("0", "1", ...)
    and ships a `legend` mapping those back to the level text -- unlike `choice`,
    which keys by option name. Worth knowing before reading either one.
    """
    return {k: v for k, v in q.items() if v is not None}


def ask(state: dict, questions: dict) -> dict:
    """One request, many questions, evaluated independently and in parallel."""
    api_key = keystore.require_api_key()

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
        got = json.loads(resp.model_dump_json()) if hasattr(resp, "model_dump_json") else dict(resp)
    except ImportError:
        pass
    except Exception as exc:  # SDK present but the call failed
        raise JevUnavailable(f"SDK call failed: {exc}") from exc
    else:
        return _require_answers(got, questions)

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
            got = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # HTTPError is file-like; closing it keeps the connection from being
        # reclaimed noisily by the GC on the error path.
        with exc:
            body = exc.read().decode("utf-8", "replace")[:300]
        raise JevUnavailable(f"HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        raise JevUnavailable(str(exc)) from exc
    return _require_answers(got, questions)


def _require_answers(resp, questions: dict) -> dict:
    """A 200 that does not answer every question is Jev failing, not deciding.

    Callers default a missing answer to a midpoint, which is right for one
    field's quirk and wrong for a whole response: `{"answers": {}}` would come
    out as a confident-looking "escalate, judged by jev" built entirely from
    those defaults. Raising here keeps it on the same exit-4 path as a 500.
    """
    answers = resp.get("answers") if isinstance(resp, dict) else None
    if not isinstance(answers, dict):
        raise JevUnavailable("response carried no answers")
    missing = sorted(k for k in questions if not isinstance(answers.get(k), dict))
    if missing:
        raise JevUnavailable(f"response did not answer: {', '.join(missing)}")
    return resp


def check() -> dict:
    """Live connectivity + credential check. Used by `probe.py --check-jev`.

    Sends the smallest possible real request rather than just hitting /models,
    because a key can be valid for listing and still fail on inference, and
    the failure we care about is the one that happens at routing time.
    """
    if not available():
        return {"ok": False, "stage": "key", "detail": keystore.SETUP_HINT}
    try:
        resp = ask({"ping": "connectivity check"},
                   {"ok": noul("This is a connectivity check")})
    except JevUnavailable as exc:
        return {"ok": False, "stage": "request", "detail": str(exc)}

    usage = resp.get("usage") or {}
    return {
        "ok": True,
        "stage": "done",
        "model_requested": MODEL,
        "model_resolved": resp.get("model"),
        "usage": usage,
        "key_source": keystore.key_source(),
        "detail": "Jev answered",
    }


def answer(resp: dict, key: str, field: str, default: float = 0.0) -> float:
    """Pull one numeric field out of a response, tolerating a missing answer."""
    got = (resp.get("answers") or {}).get(key) or {}
    val = got.get(field)
    return float(val) if isinstance(val, (int, float)) else default


def answer_choice(resp: dict, key: str, default: str = "") -> str:
    got = (resp.get("answers") or {}).get(key) or {}
    return got.get("choice") or default
