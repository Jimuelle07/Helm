#!/usr/bin/env python3
"""Route a build task to the best coding agent available on THIS machine.

This file is deliberately the single reviewable surface for the system's
judgement: every question Jev is asked and every threshold applied to its
answers lives here, so the whole decision policy can be diffed and calibrated
as one unit rather than hunted across modules.

    python route.py "add retry logic to the payment client"
    python route.py "..." --repo . --json
    python route.py "..." --execute        # opt in to actually running the agent

Pipeline:  probe.py registry -> Jev -> thresholds -> Route

The probe observes. Jev judges. The chosen agent generates. There is no branch
in that sentence: Jev is the only judge, and with no key this file exits before
it builds a single question.

The keystone is build_agent_question(): its answer space is constructed from
the agents the probe actually found, so recommending something that is not
installed is structurally impossible rather than merely unlikely.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dispatch  # noqa: E402
import jev  # noqa: E402
import keystore  # noqa: E402
import probe  # noqa: E402

JEV_MODEL = jev.MODEL
TRACE_PATH = Path(
    os.environ.get("HELM_TRACES")
    or Path.home() / ".cache" / "helm" / "decisions.jsonl"
)


# =========================================================================== #
# THE QUESTION SET
#
# All seven are answerable from the state alone, which is the real test of
# independence -- Jev evaluates them in parallel and question B never sees
# question A's answer. A genuine information dependency (for example,
# re-routing *after* an agent has failed) is a second, serial call with the
# failure added to state, not another question in this batch.
# =========================================================================== #

TASK_KINDS = {
    "scaffold": "Create a new project, service, or module from nothing",
    "feature":  "Add new behaviour to an existing codebase",
    "bugfix":   "Diagnose and correct incorrect behaviour",
    "refactor": "Restructure existing code without changing its behaviour",
    "test":     "Write, repair, or extend automated tests",
    "docs":     "Write or update documentation, comments, or explanatory prose",
    "review":   "Read and critique code without changing it",
    "research": "Investigate and report on how something works or which approach to take",
    "ops":      "Build, deploy, CI/CD, infrastructure, or environment configuration",
    "other":    "None of the above fit this task",
}

BLAST_RADIUS_LEVELS = [
    "Comments, documentation, or formatting only",
    "A single function or file already covered by tests",
    "A shared module or public API other code depends on",
    "Database migrations, authentication, billing, or deployment infrastructure",
]

SPEC_CLARITY_LEVELS = [
    "Too vague to act on without asking the user what they mean",
    "The goal is clear but the approach and target files are not",
    "The goal and rough approach are clear; some details remain open",
    "Fully specified: the change, the files, and the acceptance criteria are all stated",
]

CONTEXT_BREADTH_LEVELS = [
    "One file, in isolation",
    "A handful of related files in one module",
    "Many files across several modules in the repository",
    "The whole repository, or knowledge spanning more than one repository",
]


def build_agent_question(routable: list[dict]) -> dict:
    """The keystone. Answer space == what the probe actually found.

    Because `criteria` is built from probe output, an uninstalled agent is not a
    key in this dict, and Jev cannot return a value outside the declared space.
    "Best model available on the user's machine" therefore stops being an
    instruction we hope is respected and becomes a property of the schema.

    The `none` escape hatch is mandatory: without it an incomplete taxonomy
    forces a confident wrong answer instead of an honest abstention.
    """
    criteria = {a["name"]: a["competence"] for a in routable}
    criteria["none"] = (
        "No installed agent is a good fit for this task; escalate to the user"
    )
    return {
        "type": "choice",
        "instructions": (
            "Which of these locally installed coding agents should execute this "
            "task? Judge by the fit between the task and each agent's stated "
            "competence."
        ),
        "criteria": criteria,
    }


def build_questions(routable: list[dict]) -> dict:
    return {
        "task_kind": {
            "type": "choice",
            "instructions": "What kind of software work is the user asking for?",
            "criteria": TASK_KINDS,
        },
        "agent": build_agent_question(routable),
        "blast_radius": {
            "type": "score",
            "instructions": "If this change were wrong, how much would it damage?",
            "criteria": BLAST_RADIUS_LEVELS,
        },
        "spec_clarity": {
            "type": "score",
            "instructions": "How completely specified is this request?",
            "criteria": SPEC_CLARITY_LEVELS,
        },
        "context_breadth": {
            "type": "score",
            "instructions": "How much of the codebase must be understood to do this?",
            "criteria": CONTEXT_BREADTH_LEVELS,
        },
        "needs_human": {
            "type": "noul",
            "instructions": (
                "A human should approve this task before any agent is allowed to "
                "execute it unattended"
            ),
        },
        "reversible": {
            "type": "noul",
            "instructions": (
                "If the result is bad, it can be fully undone with a git checkout "
                "and no other cleanup"
            ),
        },
    }


# =========================================================================== #
# THRESHOLDS
#
# UNCALIBRATED. Every number below is a starting point, not a fitted value.
# Calibration is a property of a population of recorded traces: across many
# decisions, items scored 0.9 should be right more often than items scored 0.7.
# Until decisions.jsonl holds enough rows to fit against, treat these as honest
# guesses and prefer lowering automation rather than raising it.
# =========================================================================== #

THRESHOLDS = {
    "AUTO_ROUTE_MIN_CONFIDENCE": 0.75,  # below -> recommend only, never auto-execute
    "CLARIFY_MAX_SPEC_CLARITY":  1.0,   # at or below -> ask the user to clarify first
    "HUMAN_GATE_NOUL":           0.50,  # needs_human above -> require approval
    "AUTO_MAX_BLAST_RADIUS":     2.0,   # above -> require approval
    "IRREVERSIBLE_NOUL":         0.40,  # reversible below -> require approval
}


# =========================================================================== #
# Jev client -- see jev.py. Re-exported here so the thresholds, the questions
# and the failure type all read as one policy surface.
#
# Both exceptions are terminal. There is no second scorer behind them, which is
# the point: a verdict Jev did not produce is not a verdict this file will act
# on, so the only honest response to either is to say so and exit.
# =========================================================================== #

JevUnavailable = jev.JevUnavailable
MissingAPIKey = keystore.MissingAPIKey


def ask_jev(state: dict, questions: dict) -> dict:
    return jev.ask(state, questions)


# =========================================================================== #
# Compose: Verdict -> Route. Pure. No I/O, no network -- so every threshold
# below is unit-testable without touching a machine or an API.
# =========================================================================== #

def compose(verdict: dict, routable: list[dict], intent: str) -> dict:
    ans = verdict.get("answers", {})

    def num(key, field, default=0.0):
        got = ans.get(key) or {}
        val = got.get(field)
        return float(val) if isinstance(val, (int, float)) else default

    agent_name = (ans.get("agent") or {}).get("choice")
    confidence = num("agent", "confidence")
    task_kind = (ans.get("task_kind") or {}).get("choice")
    blast = num("blast_radius", "score", 1.5)
    clarity = num("spec_clarity", "score", 1.5)
    breadth = num("context_breadth", "score", 1.5)
    needs_human = num("needs_human", "noul", 0.5)
    reversible = num("reversible", "noul", 0.5)

    by_name = {a["name"]: a for a in routable}
    gates: list[str] = []

    if agent_name == "none" or agent_name not in by_name:
        if agent_name != "none":
            # Defence in depth. With a Jev Choice this is unreachable by
            # construction; it would only fire if a future edit let an unprobed
            # name into the answer space.
            gates.append(f"selected agent '{agent_name}' is not in the routable set")
        return {
            "mode": "escalate",
            "agent": None,
            "reason": "No installed agent is a good fit for this task.",
            "gates": gates or ["no suitable agent"],
            "signals": _signals(blast, clarity, breadth, needs_human,
                                reversible, confidence, task_kind),
            "source": "jev",
        }

    if clarity <= THRESHOLDS["CLARIFY_MAX_SPEC_CLARITY"]:
        gates.append(f"spec_clarity {clarity:.2f} <= {THRESHOLDS['CLARIFY_MAX_SPEC_CLARITY']}")
    if confidence < THRESHOLDS["AUTO_ROUTE_MIN_CONFIDENCE"]:
        gates.append(f"confidence {confidence:.2f} < {THRESHOLDS['AUTO_ROUTE_MIN_CONFIDENCE']}")
    if needs_human > THRESHOLDS["HUMAN_GATE_NOUL"]:
        gates.append(f"needs_human {needs_human:.2f} > {THRESHOLDS['HUMAN_GATE_NOUL']}")
    if blast > THRESHOLDS["AUTO_MAX_BLAST_RADIUS"]:
        gates.append(f"blast_radius {blast:.2f} > {THRESHOLDS['AUTO_MAX_BLAST_RADIUS']}")
    if reversible < THRESHOLDS["IRREVERSIBLE_NOUL"]:
        gates.append(f"reversible {reversible:.2f} < {THRESHOLDS['IRREVERSIBLE_NOUL']}")

    card = by_name[agent_name]
    caveats = []
    if not card.get("contract_verified"):
        caveats.append("invocation contract for this agent is unverified")
    if not card.get("auth", {}).get("ready"):
        caveats.append("no credentials detected (the agent may still be logged in)")

    signals = _signals(blast, clarity, breadth, needs_human, reversible,
                       confidence, task_kind)

    # Plan the invocation here rather than at execute time so the command the
    # user is shown, the command `--execute` runs, and the caveats about both
    # all come from one call.
    plan = dispatch.plan(card, intent, signals)
    if plan.unmet:
        caveats.append(
            f"{agent_name} cannot express {', '.join(plan.unmet)} -- "
            "see the dispatch notes")
    if plan.unverified:
        caveats.append(
            f"injected flags for {', '.join(plan.unverified)} are unverified "
            "against this CLI's --help")
    risky = [m for m in plan.advisory if m in dispatch.SAFETY_MODES]
    if risky:
        caveats.append(
            f"{', '.join(risky)} is advisory on {agent_name}, not enforced -- "
            "the agent is asked, not prevented")

    clarify = clarity <= THRESHOLDS["CLARIFY_MAX_SPEC_CLARITY"]
    return {
        "mode": "clarify" if clarify else ("auto" if not gates else "recommend"),
        "agent": agent_name,
        "display_name": card.get("display_name", agent_name),
        "command": plan.argv,
        "reason": card.get("competence", ""),
        "gates": gates,
        "caveats": caveats,
        "signals": signals,
        "dispatch": {
            "modes": list(plan.modes),
            "unavailable": list(plan.unmet),
            "unverified": list(plan.unverified),
            "advisory": list(plan.advisory),
            "notes": list(plan.notes),
        },
        "alternatives": _alternatives(ans, agent_name, by_name),
        "source": "jev",
    }


def _signals(blast, clarity, breadth, needs_human, reversible, confidence,
             task_kind=None) -> dict:
    """The metrics downstream actually acts on.

    `task_kind` rides along with the numbers because dispatch.py keys two of
    its four rules off it. Keeping it out of this block would mean every
    caller had to reach back into the raw verdict to reconstruct what the
    route already knew.
    """
    return {
        "task_kind": task_kind,
        "confidence": round(confidence, 3),
        "blast_radius": round(blast, 2),
        "spec_clarity": round(clarity, 2),
        "context_breadth": round(breadth, 2),
        "needs_human": round(needs_human, 3),
        "reversible": round(reversible, 3),
    }


def _alternatives(ans: dict, chosen: str, by_name: dict) -> list[dict]:
    probs = (ans.get("agent") or {}).get("probabilities") or {}
    out = []
    for name, p in sorted(probs.items(), key=lambda kv: -kv[1]):
        if name == chosen or name == "none" or name not in by_name:
            continue
        out.append({"agent": name, "weight": round(float(p), 3),
                    "why": by_name[name].get("competence", "")})
        if len(out) == 2:
            break
    return out


def render_command(card: dict, intent: str, signals: dict | None = None) -> list[str]:
    """The command we would run -- including whatever Jev's metrics call for.

    This is the command printed for the user, and `--execute` runs the same
    planner over the same card, so the two cannot drift. That matters more
    than it looks: the standing advice when a gate blocks auto-execution is
    "run the printed command yourself", which is only safe advice while the
    printed command is the real one, sandbox flags and all.
    """
    return dispatch.plan(card, intent, signals).argv


# =========================================================================== #
# Execution (opt-in, gated)
# =========================================================================== #

def execute(route: dict, card: dict, cwd: Path, timeout: float = 900.0,
            watch: bool = False) -> tuple[int, dict]:
    """Hand the task to the chosen agent and let Jev judge the result.

    Execution goes through supervise.py rather than a bare subprocess.run so
    that dispatch and completion-judging stay one path. The orchestrating model
    gets back a small verdict object; the worker's transcript lands on disk and
    stays out of its context unless it asks for it.
    """
    import supervise  # imported here: supervise imports probe, route imports both

    verdict = supervise.supervise(
        agent_name=route["agent"], task=route["_intent"], cwd=cwd,
        timeout=timeout, watch=watch, poll_interval=30.0,
        registry={"agents": [card], "local_inference": {"available": False, "vram_gb": None}},
        # The same signals that chose the agent also choose how to launch it.
        signals=route.get("signals"),
    )
    print("", file=sys.stderr)
    print(supervise.render(verdict), file=sys.stderr)
    return (0 if verdict["outcome"] in ("done", "uncertain") else 1), verdict


# =========================================================================== #
# Traces -- the corpus the thresholds above will eventually be calibrated on.
# Without this the numbers stay guesses forever, so it ships in v1.
# =========================================================================== #

def write_trace(intent: str, reg: dict, verdict: dict, route: dict) -> None:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "intent": intent,
        # Constant by construction now, and kept in the row anyway: every
        # historical trace carries it, and a calibration run that silently
        # mixed fallback rows into a Jev population would fit the wrong curve.
        "source": "jev",
        # Two different models, on purpose. `model_requested` is our pin;
        # `model` is the version the API says it actually ran. Recording the
        # resolved one is what makes drift detectable after the fact -- if a
        # pin is ever loosened to an alias, these stop matching and every
        # threshold fitted against the old version is suspect.
        "model_requested": JEV_MODEL,
        "model": verdict.get("model"),
        "usage": verdict.get("usage"),
        "routable": [a["name"] for a in reg["agents"] if a["routable"]],
        "answers": verdict.get("answers", {}),
        "route": {k: route.get(k) for k in ("mode", "agent", "gates", "signals")},
        "thresholds": THRESHOLDS,
    }
    try:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


# =========================================================================== #
# CLI
# =========================================================================== #

def build_state(intent: str, reg: dict, routable: list[dict]) -> dict:
    """State stays a summary. We never paste file contents in: Jev is a judge,
    not a reader, and the budget is 32k for state plus the longest question."""
    hw = reg["hardware"]
    return {
        "intent": intent,
        "machine": {
            "os": hw["os"],
            "cpu_logical_cores": hw["cpu_logical_cores"],
            "ram_total_gb": hw["ram_total_gb"],
            "max_vram_gb": hw["max_vram_gb"],
            "local_inference_available": reg["local_inference"]["available"],
        },
        "repo": reg.get("repo"),
        "agents": [
            {
                "name": a["name"],
                "competence": a["competence"],
                "strengths": a["strengths"],
                "weaknesses": a["weaknesses"],
                "context_class": a["context_class"],
            }
            for a in routable
        ],
    }


def render(route: dict, intent: str) -> str:
    mode = route["mode"]
    out = []
    if mode == "escalate":
        out.append("ESCALATE -- no installed agent fits this task")
        out.append(f"  {route['reason']}")
    elif mode == "clarify":
        out.append(f"CLARIFY FIRST -- then route to {route['agent']}")
        out.append("  The request is too underspecified to hand off safely.")
    else:
        header = "ROUTE" if mode == "auto" else "RECOMMEND"
        out.append(f"{header} -> {route['display_name']} ({route['agent']})")
        out.append(f"  why: {route['reason']}")

    if route.get("command"):
        out.append("")
        out.append("  command:")
        out.append(f"    {' '.join(route['command'])}")

    d = route.get("dispatch") or {}
    if d.get("modes") or d.get("unavailable"):
        out.append("")
        if d.get("modes"):
            out.append(f"  modes injected: {', '.join(d['modes'])}")
        if d.get("unavailable"):
            out.append(f"  modes this agent cannot express: {', '.join(d['unavailable'])}")
        risky = [m for m in (d.get("advisory") or []) if m in dispatch.SAFETY_MODES]
        if risky:
            out.append(f"  ADVISORY ONLY (asked, not enforced): {', '.join(risky)}")
        for n in d.get("notes") or []:
            out.append(f"    - {n}")

    sig = route["signals"]
    out.append("")
    out.append(
        f"  signals: kind {sig.get('task_kind')} | confidence {sig['confidence']} | "
        f"blast {sig['blast_radius']} | clarity {sig['spec_clarity']} | "
        f"breadth {sig['context_breadth']} | needs_human {sig['needs_human']} | "
        f"reversible {sig['reversible']}"
    )
    out.append(f"  judged by: {route['source']}")

    for alt in route.get("alternatives") or []:
        out.append(f"  alternative: {alt['agent']} ({alt['weight']}) -- {alt['why'][:70]}")
    for c in route.get("caveats") or []:
        out.append(f"  caveat: {c}")
    if route.get("gates"):
        out.append("")
        out.append("  not auto-executing because:")
        for g in route["gates"]:
            out.append(f"    - {g}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Route a task to the best available agent. Requires a "
                    "TypeSafe API key: Jev makes the decision, so without one "
                    "there is nothing to route with.",
        epilog="exit codes: 0 ok | 1 no routable agents | 2 no API key "
               "(--set-api-key) | 3 gates blocked --execute | 4 Jev unreachable",
        allow_abbrev=False)  # see probe.py: abbreviation made --clear-api-key a typo away
    ap.add_argument("intent", nargs="?", help="what the user wants to build")
    ap.add_argument("--repo", default=".", help="repository root for context (default: cwd)")
    ap.add_argument("--json", action="store_true", help="emit the full decision as JSON")
    ap.add_argument("--execute", action="store_true", help="run the agent if all gates pass")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="seconds to allow the agent when --execute (default: 900)")
    ap.add_argument("--watch", action="store_true",
                    help="with --execute, poll while running and stop early if the "
                         "agent gets stuck or waits for input")
    ap.add_argument("--refresh", action="store_true", help="re-probe instead of using the cache")
    ap.add_argument("--no-trace", action="store_true", help="do not append to the trace log")
    keystore.add_key_args(ap)
    args = ap.parse_args()

    key_result = keystore.handle_key_args(args)
    if key_result is not None:
        return key_result
    if args.intent is None:
        ap.error("intent is required unless --set-api-key or --clear-api-key is given")

    # Fail fast, and fail first: this runs before the probe because probing is
    # the slow part and there is nothing to do with a registry we cannot judge.
    try:
        keystore.require_api_key()
    except MissingAPIKey as exc:
        print(str(exc), file=sys.stderr)
        return 2

    repo_root = Path(args.repo).resolve()
    reg = probe.load_cached(args.refresh, repo_root, want_version=True)
    routable = [a for a in reg["agents"] if a["routable"]]

    if not routable:
        print("No routable coding agents found. Run probe.py to see what is installed.",
              file=sys.stderr)
        return 1

    state = build_state(args.intent, reg, routable)
    questions = build_questions(routable)

    try:
        verdict = ask_jev(state, questions)
    except JevUnavailable as exc:
        print(f"Jev could not answer, so there is no routing decision: {exc}",
              file=sys.stderr)
        print("  Helm does not guess when its decision layer is down. Check "
              "connectivity and your key with: python probe.py --check-jev",
              file=sys.stderr)
        return 4
    verdict["source"] = "jev"

    route = compose(verdict, routable, args.intent)

    if not args.no_trace:
        write_trace(args.intent, reg, verdict, route)

    if args.json:
        json.dump({"route": route, "verdict": verdict}, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render(route, args.intent))

    if args.execute:
        if route["mode"] != "auto":
            print("\nnot executing: gates above are unmet. "
                  "Run the printed command yourself to override.", file=sys.stderr)
            return 3
        card = next(a for a in routable if a["name"] == route["agent"])
        route["_intent"] = args.intent
        rc, _verdict = execute(route, card, repo_root,
                               timeout=args.timeout, watch=args.watch)
        return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
