#!/usr/bin/env python3
"""Translate Jev's decision metrics into agent-native invocations.

Jev answers questions about a *task* -- how much would this damage if wrong,
does a human need to approve it, how much of the repo must be understood. Every
coding-agent CLI answers the same concerns with completely different vocabulary:
Claude Code wants `--permission-mode bypassPermissions`, Gemini wants
`--approval-mode yolo`, Crush wants `--yolo`, and Aider has no concept of it at
all because it never prompts in the first place.

This module is the dictionary between the two. It is deliberately the only
place that knows both halves:

    Jev metrics  ->  mode names  ->  this agent's flags and prompt keywords
                     (dispatch)      (the card's `headless.modes` block)

Keeping it separate from supervise.py is the point. supervise.py is about
process lifecycle -- spawn, stream to disk, kill on timeout. What flags a task
deserves is a policy question, and policy that can be unit-tested without
spawning anything gets calibrated; policy tangled into a subprocess loop does
not. Everything public here is a pure function.

The mode *vocabulary* is fixed and agent-agnostic. What each mode means for a
given CLI lives in that CLI's card, so adding an agent is still a one-file
change and no Python has to learn a new flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# =========================================================================== #
# THE MODE VOCABULARY
#
# Five names, chosen because each maps onto a distinct thing a Jev metric can
# tell us. They are not a description of any one CLI's feature set -- they are
# the set of intentions worth expressing, which every card then translates.
# =========================================================================== #

MODES = {
    "unattended": (
        "Widen the agent's permissions past the headless default so it never "
        "pauses for approval. Cursor's -f, Crush's --yolo, Claude's "
        "bypassPermissions."
    ),
    "sandbox": (
        "Confine the work so a bad result cannot damage the live tree -- a git "
        "worktree or an OS sandbox. Cursor's -w, Gemini's -s, Claude's -w."
    ),
    "plan": (
        "Make the agent settle the architecture before it writes code. Aider's "
        "--architect, Goose's /plan."
    ),
    "parallel": (
        "Tell the agent to fan the work out across sub-agents rather than "
        "walking files one at a time."
    ),
    "readonly": (
        "Forbid writes outright. For review and research, where an edit is not "
        "merely unnecessary but wrong."
    ),
}

# Fixed application order, so a task that triggers several modes produces the
# same command every time. Determinism matters more than the particular order:
# an invocation that varies run to run cannot be reproduced from a trace.
MODE_ORDER = ("readonly", "sandbox", "plan", "parallel", "unattended")


# =========================================================================== #
# THRESHOLDS -- UNCALIBRATED, same caveat as route.py's and supervise.py's.
#
# These are the numbers the goal specifies, kept here as named constants rather
# than inlined in the rules so they can be fitted against decisions.jsonl later
# without touching the logic they drive.
# =========================================================================== #

THRESHOLDS = {
    "UNATTENDED_MAX_NEEDS_HUMAN": 0.30,  # below -> nobody is watching; stop asking
    "SANDBOX_MIN_BLAST_RADIUS":   2.0,   # above -> contain the blast
    "PARALLEL_MIN_BREADTH":       2.5,   # at or above -> too wide to walk serially
}

# Task kinds that must never be handed a writing agent. `review` and `research`
# both ask for a judgement, and an agent that "helpfully" applies its own
# suggestion has not done the task -- it has done a different, unrequested one.
READONLY_KINDS = frozenset({"review", "research"})

# Task kinds worth planning before coding. Scaffolding is the clearest case:
# there is no existing structure to constrain the model, so the first decision
# it makes is the one everything else inherits.
PLAN_FIRST_KINDS = frozenset({"scaffold"})


# =========================================================================== #
# Signals
# =========================================================================== #

@dataclass(frozen=True)
class Signals:
    """The subset of Jev's verdict that changes how a task should be launched.

    Every field is optional, and that is load-bearing rather than lazy. A
    missing metric means "we were not told", and the correct response to not
    being told is to inject nothing -- the base contract in the card is already
    a working headless invocation. Silence must degrade to the old behaviour,
    never to a guess that widens permissions.
    """

    task_kind: str | None = None
    blast_radius: float | None = None
    needs_human: float | None = None
    context_breadth: float | None = None
    spec_clarity: float | None = None
    reversible: float | None = None

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Signals":
        """Accept either route.py's flat `signals` block or a raw Jev verdict.

        Both shapes turn up in practice -- route.py hands its composed signals
        to supervise.py, while a caller driving supervise.py directly is more
        likely to have the verdict JSON. Reading both here means neither caller
        has to reshape anything, and a half-populated dict is fine.
        """
        if not raw:
            return cls()

        src = dict(raw)
        # A raw verdict nests everything under `answers`, with the payload key
        # naming the question type (choice / score / noul).
        answers = src.get("answers")
        if isinstance(answers, dict):
            for key, got in answers.items():
                if isinstance(got, dict):
                    for field_name in ("choice", "score", "noul"):
                        if field_name in got:
                            src.setdefault(key, got[field_name])
                            break

        def num(key: str) -> float | None:
            val = src.get(key)
            return float(val) if isinstance(val, (int, float)) else None

        kind = src.get("task_kind")
        return cls(
            task_kind=kind if isinstance(kind, str) else None,
            blast_radius=num("blast_radius"),
            needs_human=num("needs_human"),
            context_breadth=num("context_breadth"),
            spec_clarity=num("spec_clarity"),
            reversible=num("reversible"),
        )

    def empty(self) -> bool:
        return all(getattr(self, f) is None for f in
                   ("task_kind", "blast_radius", "needs_human",
                    "context_breadth", "spec_clarity", "reversible"))


# =========================================================================== #
# THE RULE TABLE
#
# One row per rule, each a predicate over Signals plus the mode it requests.
# Kept as data rather than an if-chain for the same reason route.py keeps its
# question set in one dict: the policy should be readable as a table, and a
# trace should be able to say which row fired.
# =========================================================================== #

@dataclass(frozen=True)
class Rule:
    mode: str
    because: str
    fires: Callable[[Signals], bool]


def _needs_human_low(s: Signals) -> bool:
    return (s.needs_human is not None
            and s.needs_human < THRESHOLDS["UNATTENDED_MAX_NEEDS_HUMAN"])


def _blast_high(s: Signals) -> bool:
    return (s.blast_radius is not None
            and s.blast_radius > THRESHOLDS["SANDBOX_MIN_BLAST_RADIUS"])


def _is_scaffold(s: Signals) -> bool:
    return s.task_kind in PLAN_FIRST_KINDS


def _wide_refactor(s: Signals) -> bool:
    return (s.task_kind == "refactor"
            and s.context_breadth is not None
            and s.context_breadth >= THRESHOLDS["PARALLEL_MIN_BREADTH"])


def _is_readonly_kind(s: Signals) -> bool:
    return s.task_kind in READONLY_KINDS


RULES: tuple[Rule, ...] = (
    Rule("unattended", "needs_human < 0.30 -- nobody is waiting to answer a prompt",
         _needs_human_low),
    Rule("sandbox", "blast_radius > 2.0 -- contain the damage if this goes wrong",
         _blast_high),
    Rule("plan", "task_kind is scaffold -- settle the structure before writing it",
         _is_scaffold),
    Rule("parallel", "wide refactor -- fan out rather than walking files serially",
         _wide_refactor),
    Rule("readonly", "task_kind is review or research -- an edit would be wrong",
         _is_readonly_kind),
)


def requested_modes(signals: Signals) -> list[tuple[str, str]]:
    """Which modes the metrics ask for, before any card is consulted.

    Returns (mode, why) pairs in MODE_ORDER. Conflict resolution happens here
    because it is a property of the *intentions*, not of any particular CLI:

    - `readonly` cancels `unattended`. Widening write permission for a task
      that must not write is not a trade-off, it is a contradiction, and the
      read-only intent is the one the user actually asked for.
    """
    fired = {r.mode: r.because for r in RULES if r.fires(signals)}

    if "readonly" in fired:
        fired.pop("unattended", None)

    return [(m, fired[m]) for m in MODE_ORDER if m in fired]


# =========================================================================== #
# Card mode lookup
# =========================================================================== #

def card_modes(card: dict) -> dict:
    return ((card.get("headless") or {}).get("modes") or {})


def supports(card: dict, mode: str) -> bool:
    return mode in card_modes(card)


# Modes where being merely advisory is a safety gap rather than a quality one.
# "Do not write any files" that the model may disregard is not containment, and
# a caller who reads `modes: readonly` is entitled to know which of the two
# they got.
SAFETY_MODES = frozenset({"readonly", "sandbox"})


def is_enforced(spec: dict) -> bool:
    """Does the tool guarantee this mode, or are we just asking politely?

    A flag the CLI parses is enforcement: `gemini --approval-mode plan` cannot
    write, whatever the model decides. A prompt prefix is advice -- including a
    slash directive like Goose's `/plan`, which is only as reliable as the
    agent's own handling of it. The distinction is derivable from the entry, so
    no card has to declare it and none can get it wrong.
    """
    return bool(spec.get("args") or spec.get("satisfied_by_base"))


def _strip_flags(argv: list[str], names: list[str]) -> list[str]:
    """Remove `names` and their values from argv.

    A mode often has to *replace* rather than extend: Claude's base contract
    already carries `--permission-mode acceptEdits`, and appending a second
    `--permission-mode bypassPermissions` is either ignored or an error
    depending on the parser. Neither is a failure mode worth debugging in the
    field.

    A flag's value is taken to be the next token when it does not itself look
    like a flag, so dropping a boolean flag does not swallow whatever follows
    it. That is a heuristic, but a safe one for the vocabulary these cards
    actually use -- and `replaces` is explicit in the card, so it only ever
    applies to flags an author has named.
    """
    if not names:
        return list(argv)
    drop = set(names)
    out: list[str] = []
    skip_value = False
    for i, tok in enumerate(argv):
        if skip_value:
            skip_value = False
            continue
        if tok in drop:
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            # Never swallow the task itself. A card that names a prompt-bearing
            # flag in `replaces` is a typo, and the symptom would otherwise be
            # an agent launched with no instructions and a clean exit code --
            # the exact silent no-op the supervisor exists to catch.
            skip_value = bool(nxt and not nxt.startswith("-")
                              and nxt not in ("{prompt}", "{model}"))
            continue
        out.append(tok)
    return out


# =========================================================================== #
# The plan
# =========================================================================== #

@dataclass(frozen=True)
class Plan:
    """A rendered, ready-to-spawn invocation plus why it looks the way it does."""

    argv: list[str]
    prompt: str
    modes: tuple[str, ...] = ()          # requested AND provided by this card
    unmet: tuple[str, ...] = ()          # requested but this card cannot express
    notes: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()     # applied from an unverified card entry
    advisory: tuple[str, ...] = ()       # applied by asking nicely, not by enforcement


def _expand(argv_template: list[str], flags: list[str], prompt: str) -> list[str]:
    """Substitute `{flags}` (spliced) and `{prompt}` (one element, always).

    `{prompt}` stays a single argv element for the reason the schema doc gives:
    the task text is untrusted, and string-concatenating it into a command line
    is how a quote or an ampersand turns into someone else's shell command.
    `{flags}` is the opposite -- card-authored constants, spliced in place.
    """
    out: list[str] = []
    for tok in argv_template:
        if tok == "{flags}":
            out.extend(flags)
        elif tok == "{prompt}":
            out.append(prompt)
        else:
            out.append(tok)
    return out


def base_plan(card: dict, task: str) -> Plan:
    """The card's contract with nothing injected -- the pre-Jev behaviour."""
    argv = list((card.get("headless") or {}).get("argv") or [])
    return Plan(argv=_expand(argv, [], task), prompt=task)


def plan(card: dict, task: str, signals: dict | Signals | None = None,
         *, extra_modes: tuple[str, ...] = (),
         prompt_prefix: str = "") -> Plan:
    """Render the invocation this task's metrics actually call for.

    With no signals this returns exactly `base_plan` -- the translation layer
    is additive, and an orchestrator that never learned to pass metrics keeps
    the behaviour it had.
    """
    headless = card.get("headless") or {}
    template = list(headless.get("argv") or [])
    if not template:
        return Plan(argv=[], prompt=task)

    sig = signals if isinstance(signals, Signals) else Signals.from_dict(signals)
    available = card_modes(card)

    why_by_mode = dict(requested_modes(sig))
    for m in extra_modes:
        why_by_mode.setdefault(m, "requested by the recovery plan")
    wanted = [(m, why_by_mode[m]) for m in MODE_ORDER if m in why_by_mode]

    applied: list[str] = []
    unmet: list[str] = []
    unverified: list[str] = []
    advisory: list[str] = []
    notes: list[str] = []
    flags: list[str] = []
    removals: list[str] = []
    prefixes: list[str] = []

    for mode, why in wanted:
        spec = available.get(mode)
        if not isinstance(spec, dict):
            unmet.append(mode)
            notes.append(f"wanted {mode} ({why}) but {card.get('name')} has no such mode")
            continue

        applied.append(mode)
        if not spec.get("verified", False):
            unverified.append(mode)
            # Provenance, not just a warning flag. "Came from the published
            # cheat sheet" and "was inferred" are very different levels of
            # trust, and the caller deciding whether to run the command is
            # the one who needs to tell them apart.
            notes.append(
                f"{mode}: flags not confirmed against this CLI's --help "
                f"(source: {spec.get('source', 'unknown')})")

        if not is_enforced(spec):
            advisory.append(mode)
            if mode in SAFETY_MODES:
                notes.append(
                    f"{mode} is ADVISORY on {card.get('name')} -- the agent is "
                    "instructed, not prevented. Do not treat it as containment")

        if spec.get("satisfied_by_base"):
            note = spec.get("note") or "already guaranteed by the base contract"
            notes.append(f"{mode}: {note}")
            continue

        flags.extend(spec.get("args") or [])
        removals.extend(spec.get("replaces") or [])
        if spec.get("prompt_prefix"):
            prefixes.append(spec["prompt_prefix"])

    # --- The safety interaction ------------------------------------------ #
    # Unattended and sandboxed are independently reasonable; unattended
    # *without* the sandbox it asked for is not. If the metrics wanted
    # containment and this CLI cannot provide it, the answer is to keep the
    # approval prompts, not to run wide open on the live tree.
    if "unattended" in applied and "sandbox" in unmet:
        spec = available.get("unattended") or {}
        if spec.get("satisfied_by_base"):
            # Nothing to withhold. This CLI is unattended whether we like it
            # or not -- Aider's --yes-always and `codex exec` are headless by
            # construction, and stripping that guarantee would just make the
            # run hang instead of making it safe. Say so plainly rather than
            # reporting a protection that did not happen.
            notes.append(
                f"{card.get('name')} is unattended by its base contract and cannot "
                "be made to pause, yet blast_radius called for a sandbox it cannot "
                "provide -- run it in a worktree yourself, or route elsewhere")
        else:
            applied.remove("unattended")
            for a in (spec.get("args") or []):
                if a in flags:
                    flags.remove(a)
            for r in (spec.get("replaces") or []):
                if r in removals:
                    removals.remove(r)
            if spec.get("prompt_prefix") in prefixes:
                prefixes.remove(spec["prompt_prefix"])
            if "unattended" in unverified:
                unverified.remove("unattended")
            notes.append(
                "withheld unattended: blast_radius called for a sandbox and "
                f"{card.get('name')} cannot provide one")

    # A card can declare modes but forget the `{flags}` slot in its argv. Drop
    # the flags rather than splicing them somewhere arbitrary -- and drop the
    # `replaces` with them, because stripping `--permission-mode acceptEdits`
    # without adding the replacement back would leave the agent on its own
    # default, which is the one outcome nobody asked for.
    if flags and "{flags}" not in template:
        notes.append(
            f"{card.get('name')}'s argv has no {{flags}} slot, so "
            f"{len(flags)} flag(s) could not be injected")
        flags, removals = [], []
        for m in list(applied):
            spec = available.get(m) or {}
            if spec.get("args") and not spec.get("prompt_prefix"):
                applied.remove(m)
                unmet.append(m)

    final_prompt = "".join(prefixes) + (prompt_prefix or "") + task
    argv = _expand(_strip_flags(template, removals), flags, final_prompt)

    return Plan(
        argv=argv,
        prompt=final_prompt,
        modes=tuple(applied),
        unmet=tuple(dict.fromkeys(unmet)),
        notes=tuple(notes),
        unverified=tuple(unverified),
        advisory=tuple(m for m in advisory if m in applied),
    )


# =========================================================================== #
# Recovery
#
# A verdict of `no_op` or `stuck` is not a dead end -- it is a diagnosis, and
# most agents ship the cure. Aider can `/undo` its own commit; an agent that
# described the work instead of doing it usually does it when told plainly that
# describing is not enough. What the cure *is* differs per CLI, so like the
# modes it lives in the card and this module only decides when to reach for it.
# =========================================================================== #

# Outcomes worth a second attempt. `timeout` is deliberately absent: a killed
# run may have left the tree half-modified, and re-dispatching on top of an
# unknown partial state is how one bad run becomes two. That one still
# escalates to a human.
RECOVERABLE = frozenset({"no_op", "stuck", "failed"})


@dataclass(frozen=True)
class Recovery:
    outcome: str
    prompt_prefix: str = ""
    modes: tuple[str, ...] = ()
    cleanup: list[str] = field(default_factory=list)
    note: str = ""


def recovery_for(card: dict, outcome: str) -> Recovery | None:
    """The card's declared cure for this outcome, if it has one."""
    if outcome not in RECOVERABLE:
        return None
    table = (card.get("headless") or {}).get("recovery") or {}
    spec = table.get(outcome)
    if not isinstance(spec, dict):
        return None
    return Recovery(
        outcome=outcome,
        prompt_prefix=spec.get("prompt_prefix", ""),
        modes=tuple(spec.get("modes") or ()),
        cleanup=list(spec.get("cleanup") or []),
        note=spec.get("note", ""),
    )


def should_recover(verdict: dict, rec: Recovery | None, attempt: int,
                   max_retries: int) -> tuple[bool, str]:
    """Whether to spend another run on this, and the reason either way.

    The judged_by check is the important one. Re-dispatching is an *action*,
    and this codebase's standing rule is that a degraded judge may report but
    never act: the heuristic fallback cannot tell a terse success from a no-op,
    so letting it trigger an automatic retry would re-run work that already
    succeeded. Only Jev's verdict is allowed to spend money on a second attempt.
    """
    if rec is None:
        return False, "no recovery declared for this outcome"
    if attempt >= max_retries:
        return False, f"retry budget exhausted ({max_retries})"
    if verdict.get("judged_by") != "jev":
        return False, "verdict came from the fallback judge -- too weak to act on"
    return True, rec.note or f"recovering from {rec.outcome}"


def apply_recovery(card: dict, task: str, rec: Recovery,
                   signals: dict | Signals | None) -> Plan:
    """Re-plan the same task with the card's cure folded in."""
    return plan(card, task, signals,
                extra_modes=rec.modes,
                prompt_prefix=rec.prompt_prefix)


def describe(p: Plan) -> str:
    """One line for a human, for the supervisor's rendered output."""
    bits = []
    if p.modes:
        bits.append("modes: " + ", ".join(p.modes))
    if p.unmet:
        bits.append("unavailable: " + ", ".join(p.unmet))
    return " | ".join(bits)
