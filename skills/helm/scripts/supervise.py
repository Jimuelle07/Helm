#!/usr/bin/env python3
"""Dispatch a task to a worker agent, and let Jev decide whether it finished.

    python supervise.py codex "fix the failing test in src/parser.py"
    python supervise.py claude "..." --watch --timeout 900
    python supervise.py codex "..." --json

Why this exists
---------------
"Is the worker done?" is a judgement, not a generation -- and when the
orchestrating model answers it, that is the single most expensive thing it
does. A coding agent's transcript routinely runs tens of thousands of tokens,
and reading one costs real money *and* permanently pollutes the orchestrator's
context with build noise it will carry for the rest of the session.

Jev reads it instead. Input is $0.042 per million tokens and output is free,
so judging a 50k-token transcript costs about a fifth of a cent and takes
under half a second. What comes back to the orchestrator is a verdict object
of a few hundred bytes: status, confidence, what to do next, and a path to the
full log on disk if it ever genuinely needs to look.

That is the whole trade: the transcript hits the cheap judge and the disk, and
the expensive model sees only the conclusion.

Why not just check the exit code
--------------------------------
Because it lies in both directions, and the interesting failures are the ones
it misses entirely. Coding agents exit 0 after announcing what they *would*
have done without touching a file; they exit 0 having asked a clarifying
question into a headless void; they exit non-zero over a lint warning on an
otherwise finished job. `rc == 0` cannot tell "wrote the code" from "wrote
about the code", which is exactly the distinction that matters. Hence the
`no_op` and `blocked_needs_input` categories below -- the two failures an exit
code is structurally blind to.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dispatch  # noqa: E402
import jev  # noqa: E402
import probe  # noqa: E402

RUN_DIR = Path(
    os.environ.get("HELM_RUNS")
    or Path(tempfile.gettempdir()) / "helm-runs"
)

# Excerpt budget for what Jev sees. Jev allows 32k tokens for state plus the
# longest question; this is roughly 6k tokens of transcript, leaving generous
# headroom. Weighted heavily toward the tail because that is where an agent
# says what it did, what broke, and what it is waiting for -- the head is
# mostly the task echoing back.
EXCERPT_HEAD_CHARS = 5_000
EXCERPT_TAIL_CHARS = 19_000


# =========================================================================== #
# THE COMPLETION QUESTION SET
#
# Kept here, beside its thresholds, for the same reason route.py keeps its
# own: the policy that decides "done or not" should be reviewable as one
# surface. All of these are answerable from the state alone, which is what
# makes them safe to evaluate in parallel in a single request.
# =========================================================================== #

COMPLETION_STATUS = {
    "completed": "The assigned task was carried out and the agent finished normally",
    "partial": "Real work happened but the task is visibly unfinished",
    "no_op": "The agent described, planned, or analysed the task without actually doing it",
    "blocked_needs_input": "The agent stopped to ask a question or await approval that will never arrive",
    "failed": "The agent attempted the work and it errored out",
}

FAILURE_SEVERITY_LEVELS = [
    "No errors; the run looks clean",
    "Warnings or recoverable errors that did not stop the work",
    "The task failed but the workspace looks intact",
    "Errors suggesting a broken or half-modified workspace needing cleanup",
]


def completion_questions() -> dict:
    return {
        "status": jev.choice(
            "What actually happened in this coding agent's run?", COMPLETION_STATUS),
        "task_satisfied": jev.noul(
            "The originally assigned task was genuinely carried out -- files were "
            "changed or the requested artefact produced -- rather than merely "
            "described, planned, or partially attempted"),
        "needs_human": jev.noul(
            "A human has to intervene before this work can go any further"),
        "awaiting_input": jev.noul(
            "The output ends with the agent waiting for a reply, approval, or "
            "clarification that cannot arrive because it is running headless"),
        "failure_severity": jev.score(
            "How bad is the damage or error state left behind by this run?",
            FAILURE_SEVERITY_LEVELS),
    }


def progress_questions() -> dict:
    """Cheaper set used while a run is still in flight (--watch).

    Deliberately narrow: mid-run we only need to know whether to keep waiting
    or to stop, so asking the full completion set on every poll would be
    spending latency on questions whose answers cannot yet be meaningful.
    """
    return {
        "made_progress": jev.noul(
            "Since the earlier excerpt, this agent has done further substantive "
            "work rather than idling, repeating itself, or looping"),
        "awaiting_input": jev.noul(
            "The output ends with the agent waiting for a reply, approval, or "
            "clarification that cannot arrive because it is running headless"),
    }


# =========================================================================== #
# THRESHOLDS -- UNCALIBRATED, same caveat as route.py's.
#
# These decide when the orchestrator is told "accept this" versus "look at it".
# They are conservative on purpose: wrongly reporting done means a broken task
# is silently accepted, which is far more expensive than one unnecessary check.
# =========================================================================== #

THRESHOLDS = {
    "DONE_MIN_CONFIDENCE":    0.70,  # below -> report uncertain, ask for a look
    "SATISFIED_MIN_NOUL":     0.65,  # task_satisfied must clear this to call it done
    "NEEDS_HUMAN_NOUL":       0.50,  # above -> escalate
    "AWAITING_INPUT_NOUL":    0.60,  # above -> the run is stuck, not working
    "MAX_FAILURE_SEVERITY":   2.0,   # above -> warn the workspace may be dirty
    "FALLBACK_CONFIDENCE_CAP": 0.50,  # ceiling on any non-Jev verdict
    "STUCK_POLLS_BEFORE_KILL": 3,    # consecutive no-progress polls before aborting
}

# One recovery attempt by default. The cure for a `no_op` is cheap and usually
# works first time; a budget larger than one mostly buys you the same failure
# twice at double the price, and the outcomes that survive a retry are exactly
# the ones a human should see.
DEFAULT_MAX_RETRIES = 1

# A recovery cleanup (aider's `/undo`, say) exists to put the tree back, not to
# do work. It gets its own short leash so a hung cleanup cannot eat the budget
# the actual retry needs.
CLEANUP_TIMEOUT = 120.0


# =========================================================================== #
# Dispatch
#
# What gets run is no longer just the card's argv: dispatch.py translates Jev's
# metrics into this agent's own flags and prompt keywords first. The
# translation lives there so it stays a pure function; this module only spawns
# what it is handed.
# =========================================================================== #

def render_command(card: dict, prompt: str, signals: dict | None = None) -> list[str]:
    """The argv this task would actually be launched with.

    Kept as a thin wrapper rather than inlined at the call site because
    route.py prints this command for the user to run by hand. If the printed
    command and the spawned command can drift apart, "run it yourself to
    override" quietly stops meaning what it says.
    """
    return dispatch.plan(card, prompt, signals).argv


def unsafe_on_windows(resolved: str | None, args: list[str]) -> bool:
    """Windows runs .cmd/.bat through cmd.exe, which re-parses the argument
    string -- so an untrusted prompt containing shell metacharacters is a
    command-injection vector (the BatBadBut class, CVE-2024-24576). We refuse
    rather than sanitise: cmd.exe quoting rules are genuinely hard to get
    right, and the caller can always run the printed command themselves."""
    if os.name != "nt" or not resolved:
        return False
    if Path(resolved).suffix.lower() not in {".cmd", ".bat"}:
        return False
    return bool(re.search(r'[&|<>^"%!]', " ".join(args)))


def excerpt(text: str, head: int = EXCERPT_HEAD_CHARS, tail: int = EXCERPT_TAIL_CHARS) -> str:
    """Head + tail, because a transcript's meaning lives at both ends and the
    middle is build spam. Strips ANSI first: colour codes are pure token waste
    and they break the judge's reading of the text."""
    clean = probe.strip_ansi(text).strip()
    if len(clean) <= head + tail:
        return clean
    dropped = len(clean) - head - tail
    return (f"{clean[:head]}\n\n"
            f"[... {dropped} characters of mid-run output omitted ...]\n\n"
            f"{clean[-tail:]}")


class Run:
    """A dispatched worker agent, streaming its combined output to a file.

    Output goes to disk rather than memory so the full transcript survives for
    a human (or the orchestrator, on demand) without ever being held in the
    conversation. `.text()` is a snapshot for the judge, not the source of
    truth -- the file is.
    """

    def __init__(self, argv: list[str], cwd: Path, log_path: Path):
        self.argv = argv
        self.cwd = cwd
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self.started = 0.0
        self.finished: float | None = None
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.proc = subprocess.Popen(
            self.argv, cwd=str(self.cwd), shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            with self.log_path.open("w", encoding="utf-8") as fh:
                for line in self.proc.stdout:
                    fh.write(line)
                    fh.flush()
                    with self._lock:
                        self._buf.append(line)
        finally:
            # Close the pipe explicitly. An orchestrator that dispatches many
            # agents in one process would otherwise leak a descriptor per run.
            try:
                self.proc.stdout.close()
            except OSError:
                pass

    def text(self) -> str:
        with self._lock:
            return "".join(self._buf)

    def poll(self) -> int | None:
        return self.proc.poll() if self.proc else None

    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    def wait(self, timeout: float | None) -> int | None:
        """Returns the exit code, or None if it timed out and was killed."""
        try:
            rc = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.terminate()
            self.finished = time.time()
            return None
        self.finished = time.time()
        if self._thread:
            self._thread.join(timeout=5)
        return rc

    def terminate(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        # Give the pump a moment to drain and close the pipe, so a killed run
        # still leaves a complete log behind.
        if self._thread:
            self._thread.join(timeout=5)


# =========================================================================== #
# Judging
# =========================================================================== #

def build_state(task: str, agent_name: str, output: str, rc: int | None,
                elapsed: float, timed_out: bool) -> dict:
    return {
        "assigned_task": task,
        "agent": agent_name,
        "exit_code": rc,
        "timed_out": timed_out,
        "elapsed_seconds": round(elapsed, 1),
        "output_excerpt": excerpt(output),
    }


def judge(task: str, agent_name: str, output: str, rc: int | None,
          elapsed: float, timed_out: bool) -> dict:
    state = build_state(task, agent_name, output, rc, elapsed, timed_out)
    try:
        resp = jev.ask(state, completion_questions())
        resp["source"] = "jev"
        return resp
    except jev.JevUnavailable as exc:
        v = fallback_judge(output, rc, timed_out)
        v["fallback_reason"] = str(exc)
        return v


# Phrases that mean an agent stopped to ask something. Crude next to Jev --
# literal substring checks against phrasing that varies by model and by mood --
# but they catch the loudest cases, and everything they produce is
# confidence-capped.
ASKING_MARKERS = (
    "would you like me to", "shall i proceed", "should i go ahead",
    "let me know if you", "here's what i would", "here is what i would",
    "i can help you with", "to proceed, please", "waiting for your",
    "do you want me to", "please confirm", "awaiting your",
)
FAILURE_MARKERS = (
    "traceback (most recent call last)", "fatal error", "command not found",
    "permission denied", "authentication failed", "not logged in",
    "rate limit", "quota exceeded", "unauthorized", "invalid api key",
)


def fallback_judge(output: str, rc: int | None, timed_out: bool) -> dict:
    """Heuristic completion check for when Jev is unavailable.

    Confidence is hard-capped so a degraded judge can report but never assert.
    That cap, not any single rule here, is what routes every fallback verdict
    to "review" -- the same mechanism route.py uses to keep a degraded judge
    out of the automatic path.

    Note what this deliberately does NOT try to detect: `no_op`, an agent that
    described the work instead of doing it. Telling those apart requires
    reading for meaning, which is the whole reason Jev is worth calling. An
    earlier draft guessed at it from output length and misjudged a terse but
    genuinely successful run as a no-op -- which maps to `retry`, so it would
    have re-run work that had already succeeded. A fallback that cannot tell
    should say so rather than invent a confident category.
    """
    clean = probe.strip_ansi(output)
    tail = clean[-4000:].lower()

    if timed_out:
        status, satisfied = "partial", 0.2
    elif any(m in tail for m in FAILURE_MARKERS) or (rc not in (0, None)):
        status, satisfied = "failed", 0.1
    elif any(m in tail for m in ASKING_MARKERS):
        status, satisfied = "blocked_needs_input", 0.2
    elif len(clean.strip()) < 10:
        # Exited cleanly having emitted essentially nothing. Odd, but not
        # evidence of a no-op -- just a reason not to vouch for it.
        status, satisfied = "completed", 0.25
    else:
        status, satisfied = "completed", 0.7

    return {
        "source": "fallback",
        "answers": {
            "status": {"choice": status,
                       "confidence": THRESHOLDS["FALLBACK_CONFIDENCE_CAP"]},
            "task_satisfied": {"noul": satisfied},
            "needs_human": {"noul": 0.7 if status in ("failed", "blocked_needs_input") else 0.3},
            "awaiting_input": {"noul": 0.75 if status == "blocked_needs_input" else 0.15},
            "failure_severity": {"score": 2.5 if status == "failed" else 0.5,
                                 "confidence": 0.3},
        },
    }


def compose(verdict: dict, rc: int | None, elapsed: float, timed_out: bool,
            agent_name: str, log_path: Path, output: str) -> dict:
    """Pure. Verdict -> the small object the orchestrator actually reads."""
    source = verdict.get("source", "jev")
    status = jev.answer_choice(verdict, "status", "completed")
    confidence = jev.answer(verdict, "status", "confidence")
    if source != "jev":
        confidence = min(confidence, THRESHOLDS["FALLBACK_CONFIDENCE_CAP"])

    satisfied = jev.answer(verdict, "task_satisfied", "noul", 0.5)
    needs_human = jev.answer(verdict, "needs_human", "noul", 0.5)
    awaiting = jev.answer(verdict, "awaiting_input", "noul", 0.0)
    severity = jev.answer(verdict, "failure_severity", "score", 1.0)

    notes: list[str] = []
    if timed_out:
        outcome, nxt = "timeout", "escalate"
        notes.append("killed at the timeout; work may be half-applied")
    elif awaiting > THRESHOLDS["AWAITING_INPUT_NOUL"]:
        outcome, nxt = "stuck", "ask_user"
        notes.append("agent stopped to ask something it cannot be answered headlessly")
    elif status == "failed":
        outcome, nxt = "failed", "retry"
    elif status == "no_op":
        outcome, nxt = "no_op", "retry"
        notes.append("agent described the work without doing it")
    elif status == "blocked_needs_input":
        outcome, nxt = "stuck", "ask_user"
    elif status == "partial":
        outcome, nxt = "incomplete", "retry"
    elif satisfied < THRESHOLDS["SATISFIED_MIN_NOUL"]:
        outcome, nxt = "incomplete", "review"
        notes.append(f"reported complete but task_satisfied is only {satisfied:.2f}")
    elif confidence < THRESHOLDS["DONE_MIN_CONFIDENCE"]:
        outcome, nxt = "uncertain", "review"
        notes.append(f"looks done, but confidence is only {confidence:.2f}")
    else:
        outcome, nxt = "done", "accept"

    if needs_human > THRESHOLDS["NEEDS_HUMAN_NOUL"] and nxt == "accept":
        nxt = "review"
        notes.append("judged as needing a human look")
    if severity > THRESHOLDS["MAX_FAILURE_SEVERITY"]:
        notes.append(f"workspace may be dirty (failure severity {severity:.1f}) -- check git status")
    if source != "jev":
        notes.append("judged by the heuristic fallback, not Jev -- treat as a weak signal")

    return {
        "outcome": outcome,
        "next": nxt,
        "status": status,
        "agent": agent_name,
        "exit_code": rc,
        "elapsed_s": round(elapsed, 1),
        "signals": {
            "confidence": round(confidence, 3),
            "task_satisfied": round(satisfied, 3),
            "needs_human": round(needs_human, 3),
            "awaiting_input": round(awaiting, 3),
            "failure_severity": round(severity, 2),
        },
        "notes": notes,
        "headline": _headline(output),
        "judged_by": source,
        # The orchestrator reads this path only if it decides it must. That
        # choice -- rather than the transcript arriving unbidden in context --
        # is where the token saving actually comes from.
        "log": str(log_path),
    }


def _headline(output: str) -> str:
    """Last substantive line, as a human-readable hint. Not a summary -- just
    enough for the orchestrator to say something concrete without reading the log."""
    for line in reversed(probe.strip_ansi(output).strip().splitlines()):
        line = line.strip()
        if len(line) > 12 and not set(line) <= set("-=_*#. "):
            return line[:160]
    return ""


# =========================================================================== #
# Orchestration
# =========================================================================== #

def supervise(agent_name: str, task: str, cwd: Path, timeout: float,
              watch: bool, poll_interval: float, registry: dict,
              signals: dict | None = None,
              max_retries: int = DEFAULT_MAX_RETRIES,
              use_modes: bool = True) -> dict:
    """Dispatch, judge, and -- when the card knows the cure -- try again.

    `signals` is Jev's routing verdict. Passing it is what turns a generic
    invocation into an agent-native one; omitting it reproduces the old
    behaviour exactly, which is deliberate. Nothing here should widen an
    agent's permissions on the strength of a metric nobody supplied.
    """
    agents = {a["name"]: a for a in registry["agents"]}
    card = agents.get(agent_name)
    if not card:
        return _error(agent_name, f"unknown agent '{agent_name}'")
    if not card.get("installed"):
        return _error(agent_name, f"{agent_name} is not installed on this machine")
    if card.get("auth", {}).get("state") == "unauthenticated":
        hint = probe.LOGIN_HINTS.get(agent_name, "log in to this CLI")
        return _error(agent_name,
                      f"{agent_name} is installed but not authenticated -- run: {hint}")
    if not card.get("headless"):
        return _error(agent_name, f"{agent_name} has no headless invocation contract")

    plan = (dispatch.plan(card, task, signals) if use_modes
            else dispatch.base_plan(card, task))
    if not plan.argv:
        return _error(agent_name, "no invocation contract to render")

    # One budget for the whole supervision, retries included. Giving each
    # attempt a fresh `timeout` would let a two-attempt run quietly take twice
    # as long as the caller asked for.
    deadline = time.time() + timeout
    attempts: list[dict] = []
    recovered_from: list[str] = []
    verdict: dict = {}

    for attempt in range(max_retries + 1):
        remaining = deadline - time.time()
        if remaining <= 0:
            break

        verdict = _dispatch_once(card, agent_name, task, plan, cwd,
                                 remaining, watch, poll_interval)
        attempts.append({
            "attempt": attempt + 1,
            "outcome": verdict["outcome"],
            "modes": list(plan.modes),
            "log": verdict.get("log"),
        })
        if verdict["outcome"] == "error":
            break

        rec = dispatch.recovery_for(card, verdict["outcome"])
        go, why = dispatch.should_recover(verdict, rec, attempt, max_retries)
        if not go:
            if rec is not None and attempt < max_retries:
                verdict["notes"].append(f"not retrying: {why}")
            break

        cleanup_note = _run_cleanup(card, rec, cwd, deadline)
        if cleanup_note:
            recovered_from.append(cleanup_note)
        recovered_from.append(f"{verdict['outcome']}: {why}")
        plan = dispatch.apply_recovery(card, task, rec, signals)

    if not attempts:
        return _error(agent_name, "timeout budget was exhausted before dispatch")

    verdict["attempts"] = attempts
    verdict["dispatch"] = {
        "modes": list(plan.modes),
        "unavailable": list(plan.unmet),
        "unverified": list(plan.unverified),
        "advisory": list(plan.advisory),
        "argv": plan.argv[:1] + ["..."] if plan.argv else [],
    }
    for n in plan.notes:
        verdict["notes"].append(f"dispatch: {n}")
    if plan.unverified:
        verdict["notes"].append(
            "dispatch: applied unverified flags for "
            + ", ".join(plan.unverified)
            + " -- confirm them against the CLI's --help")
    for r in recovered_from:
        verdict["notes"].append(f"recovery: {r}")
    if len(attempts) > 1:
        verdict["notes"].append(
            f"recovered and retried {len(attempts) - 1} time(s); "
            f"outcomes: {' -> '.join(a['outcome'] for a in attempts)}")
    return verdict


def _dispatch_once(card: dict, agent_name: str, task: str,
                   plan: "dispatch.Plan", cwd: Path, timeout: float,
                   watch: bool, poll_interval: float) -> dict:
    """Spawn one attempt and judge it. The loop above owns everything else."""
    argv = list(plan.argv)
    if card.get("path"):
        argv[0] = card["path"]
    if unsafe_on_windows(card.get("path"), argv[1:]):
        return _error(agent_name,
                      "refusing to execute: the prompt contains shell metacharacters "
                      "and this CLI is a Windows batch shim, which re-parses arguments "
                      "through cmd.exe. Run the command yourself, or rephrase the task.")

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    # Millisecond precision, because a recovery retry can start in the same
    # second the first attempt ended -- and two attempts sharing a log file
    # would overwrite the very transcript the retry was meant to explain.
    log_path = RUN_DIR / f"{agent_name}-{int(time.time() * 1000)}-{os.getpid()}.log"
    run = Run(argv, cwd, log_path)
    run.start()

    if watch:
        rc, timed_out = _watch_loop(run, plan.prompt, agent_name, timeout, poll_interval)
    else:
        rc = run.wait(timeout)
        timed_out = rc is None

    output = run.text()
    # Judged against the *original* task, not the prompt we decorated it with.
    # The recovery prefix is an instruction to the worker, and feeding it to
    # the judge as well would have Jev grading the agent on our scaffolding
    # rather than on what the user actually asked for.
    j = judge(task, agent_name, output, rc, run.elapsed(), timed_out)
    return compose(j, rc, run.elapsed(), timed_out, agent_name, log_path, output)


def _run_cleanup(card: dict, rec: "dispatch.Recovery", cwd: Path,
                 deadline: float) -> str | None:
    """Run a card's declared cleanup (aider's `/undo`) before the retry.

    These argv are card-authored constants with no task text in them, so the
    injection concern that governs `{prompt}` does not apply -- but they do
    touch the repository, which is why only an explicitly declared `cleanup`
    ever runs, and only for an outcome the card named.
    """
    if not rec.cleanup:
        return None
    argv = list(rec.cleanup)
    if card.get("path"):
        argv[0] = card["path"]
    budget = min(CLEANUP_TIMEOUT, max(0.0, deadline - time.time()))
    if budget <= 0:
        return "skipped cleanup: no time left in the budget"
    try:
        subprocess.run(argv, cwd=str(cwd), shell=False, timeout=budget,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"ran cleanup {' '.join(rec.cleanup)}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cleanup {' '.join(rec.cleanup)} failed ({type(exc).__name__})"


def _watch_loop(run: Run, task: str, agent_name: str, timeout: float,
                interval: float) -> tuple[int | None, bool]:
    """Poll a running agent and stop it early when it is clearly not working.

    The case this earns its keep on: an agent running headless decides to ask a
    clarifying question and then waits. Nothing is coming. Without this it
    burns the full timeout before anyone notices; with it, Jev spots the
    waiting-for-input shape on the next poll and we stop in seconds.
    """
    deadline = time.time() + timeout
    stuck_polls = 0
    last_len = 0

    while time.time() < deadline:
        rc = run.poll()
        if rc is not None:
            run.finished = time.time()
            return rc, False

        time.sleep(min(interval, max(0.0, deadline - time.time())))
        output = run.text()

        # Cheap local check first -- no point paying for a judgement when the
        # output has not changed at all since the last poll.
        grew = len(output) > last_len
        last_len = len(output)

        if not jev.available():
            if not grew:
                stuck_polls += 1
                if stuck_polls >= THRESHOLDS["STUCK_POLLS_BEFORE_KILL"]:
                    run.terminate()
                    run.finished = time.time()
                    return run.poll(), True
            else:
                stuck_polls = 0
            continue

        try:
            resp = jev.ask(
                {"assigned_task": task, "agent": agent_name,
                 "output_excerpt": excerpt(output), "still_running": True,
                 "output_grew_since_last_poll": grew},
                progress_questions())
        except jev.JevUnavailable:
            continue

        if jev.answer(resp, "awaiting_input", "noul") > THRESHOLDS["AWAITING_INPUT_NOUL"]:
            run.terminate()
            run.finished = time.time()
            return run.poll(), False

        if jev.answer(resp, "made_progress", "noul", 1.0) < 0.35:
            stuck_polls += 1
            if stuck_polls >= THRESHOLDS["STUCK_POLLS_BEFORE_KILL"]:
                run.terminate()
                run.finished = time.time()
                return run.poll(), True
        else:
            stuck_polls = 0

    run.terminate()
    run.finished = time.time()
    return run.poll(), True


def _error(agent_name: str, message: str) -> dict:
    return {
        "outcome": "error", "next": "escalate", "status": "not_started",
        "agent": agent_name, "exit_code": None, "elapsed_s": 0.0,
        "signals": {}, "notes": [message], "headline": message,
        "judged_by": "precondition", "log": None,
    }


def render(v: dict) -> str:
    lines = [f"{v['outcome'].upper()} -- {v['agent']} ({v['status']})"]
    if v.get("headline"):
        lines.append(f"  {v['headline']}")
    d = v.get("dispatch") or {}
    if d.get("modes") or d.get("unavailable"):
        bits = []
        if d.get("modes"):
            bits.append("modes " + ", ".join(d["modes"]))
        if d.get("unavailable"):
            bits.append("unavailable " + ", ".join(d["unavailable"]))
        risky = [m for m in (d.get("advisory") or []) if m in dispatch.SAFETY_MODES]
        if risky:
            bits.append("advisory only " + ", ".join(risky))
        lines.append("  " + " | ".join(bits))
    if v.get("signals"):
        s = v["signals"]
        lines.append(
            f"  confidence {s.get('confidence')} | satisfied {s.get('task_satisfied')} "
            f"| needs_human {s.get('needs_human')} | awaiting_input {s.get('awaiting_input')}")
    lines.append(f"  exit {v['exit_code']} after {v['elapsed_s']}s | judged by {v['judged_by']}")
    for n in v.get("notes", []):
        lines.append(f"  note: {n}")
    lines.append(f"  next: {v['next']}")
    if v.get("log"):
        lines.append(f"  full transcript: {v['log']}")
    return "\n".join(lines)


def _load_signals(inline: str | None, path: str | None) -> dict | None:
    """Read the routing verdict from --signals or --signals-file.

    A file is offered because the JSON is usually piped straight out of
    `route.py --json`, and round-tripping that through a shell argument is how
    a quote gets eaten and the metrics silently arrive empty -- which would
    look exactly like "no modes needed".
    """
    if path:
        raw = Path(path).read_text(encoding="utf-8")
    elif inline:
        raw = inline
    else:
        return None
    got = json.loads(raw)
    if not isinstance(got, dict):
        raise ValueError("expected a JSON object")

    # Three shapes turn up, and all three are accepted so no caller has to
    # reshape anything: `route.py --json` in full, something carrying a
    # `signals` block, or a raw Jev verdict. Flat keys always win over
    # anything nested under `answers`, which Signals.from_dict unpacks.
    merged: dict = {}
    if isinstance(got.get("verdict"), dict):
        merged.update(got["verdict"])

    inner = got.get("route") if isinstance(got.get("route"), dict) else got
    if isinstance(inner.get("signals"), dict):
        merged.update(inner["signals"])
        if inner.get("task_kind"):
            merged["task_kind"] = inner["task_kind"]
        return merged

    # No envelope to unwrap: it is already the metrics themselves.
    return merged or got


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Dispatch a task to an agent and let Jev judge completion.",
        allow_abbrev=False)
    ap.add_argument("agent", help="agent name, e.g. codex (see probe.py)")
    ap.add_argument("task", help="the task to hand to that agent")
    ap.add_argument("--repo", default=".", help="working directory (default: cwd)")
    ap.add_argument("--timeout", type=float, default=900.0, help="seconds (default: 900)")
    ap.add_argument("--watch", action="store_true",
                    help="poll while running and stop early if stuck or waiting for input")
    ap.add_argument("--poll-interval", type=float, default=30.0,
                    help="seconds between polls when --watch (default: 30)")
    ap.add_argument("--signals", metavar="JSON",
                    help="Jev's routing verdict, as JSON -- either route.py's "
                         "`signals` block or a raw verdict. Drives which "
                         "agent-native flags and keywords get injected.")
    ap.add_argument("--signals-file", metavar="PATH",
                    help="read --signals from a file instead (avoids shell quoting)")
    ap.add_argument("--no-modes", action="store_true",
                    help="dispatch with the card's base contract only, injecting nothing")
    ap.add_argument("--retry", type=int, default=DEFAULT_MAX_RETRIES,
                    metavar="N",
                    help=f"recovery attempts after a no_op/stuck/failed verdict "
                         f"(default: {DEFAULT_MAX_RETRIES}; 0 disables)")
    ap.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    args = ap.parse_args()

    try:
        signals = _load_signals(args.signals, args.signals_file)
    except (OSError, ValueError) as exc:
        ap.error(f"could not read --signals: {exc}")

    cwd = Path(args.repo).resolve()
    registry = probe.load_cached(False, None, want_version=True)
    verdict = supervise(args.agent, args.task, cwd, args.timeout,
                        args.watch, args.poll_interval, registry,
                        signals=signals, max_retries=max(0, args.retry),
                        use_modes=not args.no_modes)

    if args.json:
        json.dump(verdict, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(render(verdict))

    return {"done": 0, "uncertain": 0}.get(verdict["outcome"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
