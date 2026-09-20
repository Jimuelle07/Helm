# Walkthrough: install to end-to-end

A complete run-through with real output. Every block below is copied from an actual
session on a Windows 11 machine with seven agent CLIs installed — including the parts
that went wrong, because those are the ones worth seeing.

You will watch Helm inventory the machine (§1), then hand every judgement in the loop to
TypeSafe Jev: which agent should take the task (§4), which flags it should run with (§5),
and whether it actually finished (§5–6). Note the costs as they go by — the whole
walkthrough, routing and completion-judging included, runs for **under a cent**. That
price is the reason orchestration can afford to ask before every task rather than guess.

Total time: about five minutes.

---

## 0. Install

```bash
# from skills.sh
skills add helm

# or drop the folder into your skills directory
git clone <repo> && cp -r . ~/.claude/skills/helm
```

There is nothing to build and nothing to `pip install`. The scripts use only the Python
standard library, so if `python3 --version` prints 3.10 or newer you are done. (Tested on
3.12, 3.14 and 3.15.)

Verify:

```bash
cd ~/.claude/skills/helm
python scripts/probe.py --repo .
```

---

## 1. First run: what does this machine actually have?

```
$ python scripts/probe.py --repo .
```

```
MACHINE
  Windows 11 (AMD64)  |  Intel64 Family 6 Model 186 Stepping 2, GenuineIntel
  12 logical cores  |  RAM 15.7 GB total, 2.7 GB free  |  disk 52.4 GB free
  GPU: NVIDIA GeForce RTX 4050 Laptop GPU (6.0 GB, via nvidia-smi)
  Local inference: unavailable (no runtime installed)

ROUTABLE AGENTS (7)
  aider          aider.EXE 0.86.2            [auth unverified]
                 Surgical edits to one or a few named files, committed to git
                 automatically; needs the target to be identified up front
  claude         2.1.278 (Claude Code)       [auth verified]
                 Long-horizon multi-file work: architecture, cross-cutting refactors,
                 and tasks where the plan itself has to be worked out first
  codex          codex-cli 0.153.4           [auth verified]
                 Well-scoped implementation and debugging inside a codebase that
                 already exists, especially iterating against a failing test
  copilot        GitHub Copilot CLI 1.0.83.  [auth unverified]
  cursor-agent   2026.09.10-fd3934a          [auth verified]
                 Edits that first require finding every call site across an
                 unfamiliar repository, using a semantic index rather than grep
  gemini         0.58.0                      [auth unverified]
  opencode       1.18.31                     [auth verified]

NOT INSTALLED (6): amp, crush, droid, goose, ollama, qwen

DECISION LAYER (required)
  Jev: NO API KEY -- route.py and supervise.py will not run.
       Helm probes the machine, Jev decides what to do with it;
       there is no second scorer behind it.
       Set one with:  python probe.py --set-api-key apikey_...
```

Note the exit code: `2`, not `0`. The inventory still prints — that is exactly what you
need to see while setting up — but nothing downstream will run until §2 is done.

First run takes ~12 seconds because it runs every CLI's `--version` and auth check in
parallel. It then caches for 24 hours; subsequent runs are **0.4 seconds**.

Three things to notice:

**Seven agents, four model families, and none of them can see the other six.** That is the
problem this exists for.

**`Local inference: unavailable`.** `~/.ollama/models` exists on this machine but is empty,
and a stale `...\Programs\Ollama` entry sits in `PATH` pointing at a deleted directory.
Neither is an installation, so no route will ever suggest a local model here. With 6 GB of
VRAM it would cap out around a 10B four-bit model anyway.

**`auth verified` vs `auth unverified`.** Four CLIs expose a status command we can ask;
three don't. Unverified never blocks — see §3.

---

## 2. Configure Jev — this is required

§1 is the only part that works without a key, and even it exits non-zero. Jev is the
decision layer: the probe observes, Jev judges, the chosen agent generates. Remove the
middle term and there is no routing decision to make, so `route.py` exits with setup
instructions rather than producing something weaker that looks like an answer.

Add **your own** TypeSafe key (the skill ships without one; each person uses their own
account):

```bash
$ python scripts/route.py --set-api-key apikey_2272292763ff07842bf8e1...

Stored TypeSafe API key (apike...bf32) at ~/.cache/helm/credentials.json
Helm's decision layer is configured. Confirm it works with: python probe.py --check-jev
```

Confirm it actually works, with one live request:

```bash
$ python scripts/probe.py --check-jev

Jev OK -- Jev answered
  key source      : stored
  model requested : jev-1.13.0
  model resolved  : jev-1.13.0
  usage           : 281 in / 20 out  (~$0.0000118)
```

This sends a real inference request rather than just listing models, because a key can be
valid for listing and still fail at inference — and the failure that matters is the one
that happens at routing time.

### What it looks like when you skip this

```bash
$ python scripts/route.py "fix the failing test in src/parser.py"

No TypeSafe API key configured -- Jev is Helm's decision layer and is required.
  fix: python route.py --set-api-key apikey_...
       (or export TYPESAFE_API_KEY=apikey_... for this session)
  Get a key at https://typesafe.ai -- then verify with: python probe.py --check-jev
$ echo $?
2
```

No probe runs, no partial answer, no recommendation you might mistake for a judged one.
A bad key fails the same way a little later, with exit `4` and the API's own error text —
the check happens before the probe, the network call after it.

---

## 3. The auth faultline

Here is the case this catches. Suppose you installed Codex last week and never logged in:

```
INSTALLED BUT NOT AUTHENTICATED (1)  <- log in to use these
  codex          Not logged in.
                 fix: codex login
```

`codex` is now **excluded from routing entirely**, so no task gets handed to a CLI that
cannot call a model. Run `codex login`, then re-run anything — logged-out agents are
re-checked on every invocation rather than waiting out the 24-hour cache, because being
stale right after telling someone to go log in is the worst possible moment for it.

**The rule is deliberately asymmetric**, and it matters:

| Signal | Meaning | Blocks routing? |
|---|---|---|
| No credential found in env or config | We couldn't see one | **No** |
| The tool's own status command says logged out | Authoritative | **Yes** |

On this machine the file-and-env scan reported "no credentials detected" for
`cursor-agent`, while `cursor-agent status` reported a live session. Tokens live in OS
keychains and browser sessions no probe can enumerate. Had that scan been allowed to gate,
a perfectly good agent would have silently vanished from the answer space.

---

## 4. Route a task

```bash
$ python scripts/route.py "fix the off-by-one in parse_header in src/http/headers.py, \
    test_parse_header_boundary is failing" --repo .
```

```
RECOMMEND -> OpenAI Codex CLI (codex)
  why: Well-scoped implementation and debugging inside a codebase that already
       exists, especially iterating against a failing test until it passes

  command:
    codex exec --sandbox workspace-write fix the off-by-one in parse_header in ...

  signals: confidence 0.43 | blast 1.18 | clarity 2.56 | breadth 0.27 |
           needs_human 0.25 | reversible 0.73
  judged by: jev
  alternative: aider (0.49) -- Surgical edits to one or a few named files ...

  not auto-executing because:
    - confidence 0.43 < 0.75
```

1.1 seconds, ~2,160 input tokens, **$0.00009**.

Read the signals: `blast 1.18` is "a single tested function", `clarity 2.56` is well
specified, `breadth 0.27` is one file in isolation. All correct.

The interesting part is `confidence 0.43`. Look at the alternative: `aider` scored 0.49
against codex's 0.51. That is a genuine near-tie — both are good at surgical test-driven
fixes — and the low confidence is Jev correctly reporting that the choice is close. The
gate fires and it recommends rather than auto-running. **That is the system working, not
failing.**

### When every gate passes

```bash
$ python scripts/route.py "update the README install section to mention Python 3.10"
```

```
ROUTE -> Aider (aider)
  signals: confidence 1.0 | blast 0.0 | clarity 1.94 | breadth 0.0 |
           needs_human 0.24 | reversible 0.74
  judged by: jev
  caveat: no credentials detected (the agent may still be logged in)
```

`ROUTE`, not `RECOMMEND` — this is `auto` mode. Confidence 1.0, blast 0.0 ("docs only"),
breadth 0.0 ("one file"). Every gate passes.

Confidence 1.0 here is Jev reporting that the choice is not close, which is the opposite
of the §4 near-tie above — and worth contrasting, because it is the same threshold doing
the work in both directions. The auth caveat shows up without blocking: the faultline
again.

---

## 5. Dispatch, and let Jev tell you when it's done

Now the part that saves the most tokens. Here is an actual first run against a real repo —
including the failure, because it is the best possible demonstration.

```bash
$ python scripts/supervise.py codex \
    "add a one-line docstring to the greet function in greet.py" --repo ./demo
```

```
FAILED -- codex (failed)
  return "Hello, " + name
  confidence 0.65 | satisfied 0.04 | needs_human 0.89 | awaiting_input 0.41
  exit 0 after 31.4s | judged by jev
  next: retry
  full transcript: /tmp/helm-runs/codex-1789882180-5804.log
```

**Exit code 0. Nothing happened.** From the transcript:

```
ERROR codex_core::tools::router: error=patch rejected: writing is blocked by
      read-only sandbox; rejected by user approval settings

I couldn't modify `greet.py` because the workspace is read-only.
The requested change is:
    """Greet someone by name."""
```

`git status` was clean. The agent described the edit it could not make, and exited
successfully. **An `rc == 0` check would have reported this as done.** Jev gave it
`satisfied 0.04` and `needs_human 0.89`.

This was a genuine bug in the capability card, found by exactly this mechanism: `codex
exec` defaults to a read-only sandbox, so the headless contract could not write. Fixed by
adding `--sandbox workspace-write`. Re-running the identical command:

```
DONE -- codex (completed)
  Added a one-line docstring to `greet` in `greet.py`. No other files were changed.
  confidence 1.0 | satisfied 0.98 | needs_human 0.05 | awaiting_input 0.07
  exit 0 after 69.4s | judged by jev
  next: accept
  full transcript: /tmp/helm-runs/codex-1789882299-14628.log
```

```diff
 def greet(name):
+    """Greet someone by name."""
     return "Hello, " + name
```

**Same exit code both times. Opposite reality.** `satisfied` went 0.04 → 0.98. That single
distinction is the entire argument for a semantic judge, and it showed up on the first real
dispatch rather than in a contrived example.

### What the orchestrator actually reads

The transcript above ran ~6,500 tokens. What comes back to the calling model is the verdict
block — roughly 300 bytes — plus a path. Judging cost a fraction of a cent; reading it with
a frontier model would have cost meaningfully more *and* left 6,500 tokens of build noise
in context for the rest of the session.

Act on `next`. Open the log only if it says `review`.

| `outcome` | `next` | Meaning |
|---|---|---|
| `done` | `accept` | Carried out, high confidence |
| `uncertain` | `review` | Looks finished, judge not confident |
| `incomplete` | `review` | Claimed done; task not actually satisfied |
| `no_op` | `retry` | Described the work instead of doing it |
| `stuck` | `ask_user` | Waiting on input that cannot arrive headlessly |
| `failed` | `retry` | Errored out |
| `timeout` | `escalate` | Killed at the deadline; may be half-applied |
| `error` | `escalate` | Precondition failed (not installed, not logged in) |

### Long runs

```bash
$ python scripts/supervise.py claude "large refactor..." --watch --timeout 1800
```

`--watch` polls while the run is in flight and stops it early if Jev sees it looping or
waiting for input. That second case earns the flag on its own: an agent running headless
sometimes asks a clarifying question and then waits for an answer that can never arrive,
burning the full timeout before anyone notices.

---

## 6. The same task, launched three different ways

Routing picks *who*. The dispatch layer picks *how* — translating Jev's metrics into that
agent's own flags before the command is ever printed. Three real runs:

```bash
$ python scripts/route.py "refactor the payment client to use the new retry helper     across every call site in the repo"
```

```
RECOMMEND -> Cursor Agent (cursor-agent)
  command:
    cursor-agent -w -p refactor the payment client to use the new retry helper ...

  modes injected: sandbox

  signals: kind refactor | confidence 0.85 | blast 2.5 | clarity 1.25 |
           breadth 2.47 | needs_human 0.73 | reversible 0.54
  caveat: injected flags for sandbox are unverified against this CLI's --help
```

`blast 2.5` crossed the 2.0 line, so the change runs in a git worktree instead of the live
tree. The caveat is the honest half: `cursor-agent` was not installed on the machine where
these cards were written, so `-w` came from the cheat sheet rather than from `--help`.

```bash
$ python scripts/route.py "review src/parser.py for off-by-one errors and report what you find"
```

```
RECOMMEND -> Gemini CLI (gemini)
  command:
    gemini --approval-mode plan -p review src/parser.py for off-by-one errors ...

  modes injected: readonly
  signals: kind review | confidence 0.55 | blast 1.36 | clarity 2.24 | breadth 0.08 ...
```

The base contract is `--approval-mode auto_edit`. Because the task kind is `review`, that
flag was *replaced* rather than extended: the agent now literally cannot write. An agent
that helpfully applies its own review suggestion has not done the task — it has done a
different, unrequested one.

```bash
$ python scripts/route.py "create a new FastAPI service from scratch with health checks,     structured logging and a Dockerfile"
```

```
RECOMMEND -> Claude Code (claude)
  command:
    claude -p --permission-mode acceptEdits -w Before writing any code, work out the
    structure: which files to create, what each is responsible for, and the interfaces
    between them. State that plan, then implement it. Task: create a new FastAPI ...

  modes injected: sandbox, plan
  signals: kind scaffold | confidence 0.99 | blast 2.77 | clarity 1.55 | breadth 1.25 ...
```

Two rules fired at once: `sandbox` from the blast radius, and `plan` from the task kind.
The prompt is decorated, the task text stays a single argv element, and the whole thing is
reproducible — modes apply in a fixed order, so the same signals always render the same
command.

### What happens with no signals

Nothing. `python scripts/supervise.py codex "..."` with no `--signals` renders the card's
base contract byte for byte, and a test asserts that for every shipped card. The
alternative is a system that quietly starts running `--yolo` the day an orchestrator
forgets to pass a metric.

### Recovery

When Jev returns `no_op`, `stuck` or `failed` and the card declares a cure, the supervisor
injects it and tries once more:

```
DONE -- claude (completed)
  note: recovery: no_op: re-dispatch with an explicit apply-the-edits instruction
  note: recovered and retried 1 time(s); outcomes: no_op -> done
```

Aider gets `/undo` run against it first, because it auto-commits and the failed attempt is
already in the history. A `timeout` never retries — the tree may be half-modified. Neither
does an **unjudged** run: if Jev went down after the agent finished, nobody knows what it
did to the workspace, and re-running it blind is how one bad run becomes two. That verdict
comes back as `error` / `next: escalate` with the transcript path attached.

See `references/dispatch-modes.md` for the rule table and the card schema.

---

## 7. Teaching it a tool it doesn't know

Drop one JSON file in `scripts/cards/`:

```jsonc
{
  "name": "mytool",
  "display_name": "My Tool",
  "bin_names": ["mytool"],
  "competence": "Short, contrastive line describing what this tool is uniquely good at",
  "strengths": ["..."], "weaknesses": ["..."],
  "task_fit": { "scaffold": 2, "feature": 4, "bugfix": 5, "refactor": 3, "test": 4,
                "docs": 2, "review": 3, "research": 0, "ops": 1, "other": 2 },
  "context_class": "medium",
  "headless": {
    "argv": ["mytool", "--yes", "{flags}", "-p", "{prompt}"],
    "modes": {
      "unattended": { "satisfied_by_base": true, "verified": true },
      "sandbox":    { "args": ["--worktree"], "verified": true },
      "readonly":   { "args": ["--dry-run"], "verified": true }
    },
    "recovery": {
      "no_op": { "prompt_prefix": "Your previous run described this work instead of doing it. Make the edits now. Task: " }
    }
  },
  "auth_check": { "argv": ["mytool", "status"], "ok_pattern": "logged in" },
  "contract_verified": true
}
```

Then `python scripts/probe.py --refresh`.

Two things the demo above should make concrete:

- **The `competence` line is a tuned parameter, not documentation.** It is the literal text
  Jev reads when choosing. Write it to *discriminate* against the other cards.
- **Make sure `argv` can actually write.** Most agent CLIs default to prompting or a
  read-only sandbox in headless mode, which produces the silent no-op from §5. Check for a
  `--yes` / `--auto` / `--sandbox` / `--permission-mode` flag and include it.
- **`argv` must work with `{flags}` expanding to nothing.** The base contract is what runs
  for any task that carries no metrics, so it has to be a complete, runnable, headless
  invocation on its own. `modes` only ever *adds* to it.
- **Set `verified` honestly on every mode.** A wrong prompt prefix costs some quality; a
  wrong flag costs the entire run. If you cannot check it against `--help`, prefer a
  `prompt_prefix` or leave the mode out — `unavailable: sandbox` is a useful signal, and a
  flag that kills the process is not.

Run `python -m unittest discover -s tests` afterwards — the card tests catch missing
`task_fit` keys, a bad `context_class`, and competence lines duplicated from another card.

---

## 8. Cheat sheet

```bash
# inventory (cached 24h)
python scripts/probe.py --repo .
python scripts/probe.py --refresh          # force re-probe
python scripts/probe.py --json             # machine-readable registry
python scripts/probe.py --no-verify-auth   # skip login checks, faster

# credentials
python scripts/probe.py --check-jev        # one live request
python scripts/route.py --set-api-key apikey_...
python scripts/route.py --clear-api-key

# routing
python scripts/route.py "task" --repo .
python scripts/route.py "task" --json      # full verdict + raw answers
python scripts/route.py "task" --execute   # run it, if every gate passes

# dispatch + completion judging
python scripts/supervise.py codex "task" --repo .
python scripts/supervise.py claude "task" --watch --timeout 1800
python scripts/supervise.py codex "task" --json

# task-tuned dispatch (agent-native flags from Jev's metrics)
python scripts/route.py "task" --json > /tmp/d.json
python scripts/supervise.py claude "task" --signals-file /tmp/d.json
python scripts/supervise.py claude "task" --signals '{"blast_radius": 3.0}'
python scripts/supervise.py claude "task" --no-modes   # base contract only
python scripts/supervise.py claude "task" --retry 0    # no recovery attempt

# tests
python -m unittest discover -s tests               # 224, offline, free
HELM_LIVE=1 python -m unittest tests.test_jev_live   # 18, live API
```

State lives in `~/.cache/helm/` — `credentials.json`, `registry.json` (the cache),
and `decisions.jsonl` (every routing decision, for calibrating thresholds later). Run logs
go to your temp directory. Override any of it with `HELM_CREDENTIALS`,
`HELM_CACHE`, `HELM_TRACES`, `HELM_RUNS`.

---

## 9. What to expect to go wrong

**`No TypeSafe API key configured` (exit 2).** Helm has no decision layer. Run
`route.py --set-api-key apikey_...`, then `probe.py --check-jev` to confirm. There is no
degraded mode to fall back on and this is deliberate — see `jev-decision-layer.md`, "Why
there is no fallback".

**`Jev could not answer` (exit 4).** The key is present but the API rejected it or could
not be reached; the message carries the API's own error text. `probe.py --check-jev` names
the failing stage.

**A run came back `error` with "the agent ran but Jev could not judge the result".** The
agent really did execute and the workspace may have been modified — Helm simply has no
verdict on it. Read the transcript at the path in the output and check `git status`. Do not
re-dispatch blind.

**An agent you have isn't listed.** Either there is no card for it (§6), or the binary isn't
on `PATH`. The probe resolves real files only — a `PATH` entry pointing at a deleted
directory doesn't count, which is how the stale Ollama entry gets correctly ignored.

**Everything routes to one agent.** Usually the `competence` lines are too similar to
discriminate. Rewrite them to contrast, not to describe.

**A dispatch returns `no_op` repeatedly.** Almost certainly the §5 bug in that agent's card:
its headless contract can't write. Check the transcript for a sandbox or approval error.

**The thresholds feel wrong.** They are uncalibrated guesses, labelled as such everywhere.
Every decision is logged to `decisions.jsonl` so they can be fitted against real outcomes —
`references/calibration.md` explains the method, including why you should check whether
confidence is *calibrated* before moving any threshold.
