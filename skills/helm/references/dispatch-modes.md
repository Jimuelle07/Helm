# Dispatch Modes

How a Jev decision becomes an actual command line.

Jev answers questions about a *task*: how much would this damage if it were wrong, does a
human need to approve it, how much of the repo must be understood. Every agent CLI answers
those same concerns in its own vocabulary — Claude Code wants `--permission-mode
bypassPermissions`, Gemini wants `--approval-mode yolo`, Crush wants `--yolo`, and Aider
has no concept of it at all because it never prompts in the first place.

`scripts/dispatch.py` is the dictionary between the two, and it is deliberately the only
place that knows both halves:

```
Jev metrics  ->  mode names  ->  this agent's flags and prompt keywords
                 (dispatch.py)    (the card's headless.modes block)
```

The mode *vocabulary* is fixed and agent-agnostic. What each mode means for a given CLI
lives in that CLI's card, so teaching Helm a new agent is still a one-file change and no
Python has to learn a new flag.

## The five modes

| Mode | Intent | Examples |
|---|---|---|
| `unattended` | Widen permissions past the headless default so it never pauses | Cursor `-f`, Crush `--yolo`, Claude `--permission-mode bypassPermissions` |
| `sandbox` | Confine the work so a bad result cannot touch the live tree | Claude `-w`, Gemini `-s`, Cursor `-w` |
| `plan` | Settle the architecture before writing code | Aider `--architect`, Goose `/plan` |
| `parallel` | Fan out across sub-agents instead of walking files serially | Claude's "Fan out subagents in parallel to …" |
| `readonly` | Forbid writes outright | Claude/Gemini plan mode, Codex `--sandbox read-only`, Aider `--dry-run` |

## The rules

Four rules map metrics onto modes, plus one for read-only work. Every threshold is a named
constant in `dispatch.THRESHOLDS`, and all of them are **uncalibrated** — the same caveat
as `route.py`'s, for the same reason.

| Rule | Fires when | Mode |
|---|---|---|
| 1 | `needs_human < 0.30` | `unattended` |
| 2 | `blast_radius > 2.0` | `sandbox` |
| 3 | `task_kind == "scaffold"` | `plan` |
| 4 | `task_kind == "refactor"` and `context_breadth >= 2.5` | `parallel` |
| 5 | `task_kind` in `review`, `research` | `readonly` |

Rule 4 is a conjunction on purpose. A narrow refactor is not a fan-out job, and a wide
*feature* is a different shape of work; either half alone would fire it far too often.

### Silence changes nothing

A metric that was not supplied fires no rule. `dispatch.plan(card, task, None)` returns
exactly the card's base contract, byte for byte — a test asserts this for every shipped
card. This is the most important property in the module: the alternative is a system that
quietly starts running `--yolo` the day an orchestrator forgets to pass a signal.

### Two conflicts, resolved deliberately

**`readonly` cancels `unattended`.** Widening write permission for a task that must not
write is not a trade-off to balance, it is a contradiction, and the read-only intent is the
one the user actually asked for.

**`unattended` is withheld when the `sandbox` it asked for is unavailable.** Running wide
open is reasonable *inside* a worktree and reckless on the live tree. If blast radius called
for containment and this CLI cannot provide it, the approval prompts stay.

There is one honest exception. Aider's `--yes-always` and `codex exec` are unattended by
construction: stripping that would make the run hang, not make it safe. For those the
planner reports the situation instead of claiming a protection it did not apply —

```
aider is unattended by its base contract and cannot be made to pause, yet blast_radius
called for a sandbox it cannot provide -- run it in a worktree yourself, or route elsewhere
```

## Card schema

The mode table lives under `headless.modes`. Each entry says how that mode is achieved:

```jsonc
"headless": {
  "argv": ["claude", "-p", "--permission-mode", "acceptEdits", "{flags}", "{prompt}"],
  "modes": {
    "unattended": {
      "args": ["--permission-mode", "bypassPermissions"],
      "replaces": ["--permission-mode"],
      "verified": true,
      "note": "skips every permission check; the base contract only auto-accepts edits"
    },
    "sandbox":  { "args": ["-w"], "verified": true },
    "plan":     { "prompt_prefix": "Before writing any code, …", "verified": true },
    "readonly": { "args": ["--permission-mode", "plan"],
                  "replaces": ["--permission-mode"], "verified": true }
  }
}
```

| Field | Meaning |
|---|---|
| `args` | Flags spliced in at the `{flags}` slot |
| `replaces` | Flag names to strip from the base argv first, with their values |
| `prompt_prefix` | Text prepended to the task. Prefixes apply in `MODE_ORDER`, so the command is reproducible |
| `satisfied_by_base` | This CLI already guarantees the mode; inject nothing and say so |
| `verified` | Have these `args` been confirmed against the CLI's own `--help`? |
| `note` | Shown to the user in the dispatch notes |

### `{flags}`

Flag injection needs an explicit insertion point, because "before the prompt" is wrong for
`cursor-agent -p {prompt}` and right for `codex exec {prompt}`. A card that declares
flag-based modes must therefore carry a `{flags}` token in its `argv`; a test enforces it.
Unlike `{prompt}`, which is always exactly one argv element, `{flags}` splices — it holds
card-authored constants, never task text.

### `replaces`

Some modes must replace rather than extend. Claude's base contract already carries
`--permission-mode acceptEdits`, and appending a second `--permission-mode` is either
ignored or an error depending on the parser — neither being a failure worth debugging in
the field. A named flag's value is taken to be the next token unless that token is itself a
flag, so dropping a boolean flag does not swallow whatever follows it.

### `verified`

Same discipline as `contract_verified`, and just as load-bearing. `true` means the flags in
`args` were confirmed against that CLI's own `--help`. A wrong flag is not a degraded run,
it is a dead one.

Confirmed against a live `--help`: **claude, codex, gemini, aider, opencode, copilot**.

Not confirmed — the remaining seven were not installed on the machine where these cards
were written: **cursor-agent, crush, goose, droid, amp, qwen, ollama**. Their flag-bearing
entries are marked `"verified": false` and the planner surfaces that as a caveat:

```
caveat: injected flags for sandbox are unverified against this CLI's --help
```

Where no flag could be quoted from a source, those cards use a `prompt_prefix` instead.
That is a deliberate asymmetry: a wrong prompt prefix costs some quality, a wrong flag
costs the whole run.

## Recovery

A verdict of `no_op` or `stuck` is a diagnosis, not a dead end — and most agents ship the
cure. The card declares it under `headless.recovery`, keyed by outcome:

```jsonc
"recovery": {
  "no_op": {
    "prompt_prefix": "Your previous run described this work instead of doing it. …",
    "modes": ["unattended"],
    "note": "re-dispatch with an explicit apply-the-edits instruction"
  },
  "failed": {
    "cleanup": ["aider", "--message", "/undo", "--yes-always"],
    "prompt_prefix": "A previous attempt failed and its commit has been rolled back. …",
    "note": "aider auto-commits, so /undo rolls the failed attempt back first"
  }
}
```

`cleanup` runs before the retry and is how an agent undoes its own mess. It is card-
authored argv with no task text in it, it gets its own short timeout, and only an outcome
the card explicitly named will ever trigger it.

### When recovery actually fires

Three conditions, all required:

1. The outcome is in `dispatch.RECOVERABLE` — `no_op`, `stuck`, or `failed`.
2. The card declares a cure for that outcome.
3. **The verdict came from Jev, not the heuristic fallback.**

The third is the one worth arguing about. Re-dispatching is an *action*, and this
codebase's standing rule is that a degraded judge may report but never act. The fallback
cannot tell a terse success from a no-op — it says so in its own docstring — so letting it
trigger a retry would re-run work that had already succeeded. The verdict then carries
`not retrying: verdict came from the fallback judge -- too weak to act on`.

`timeout` is deliberately **not** recoverable. A killed run may have left the tree
half-modified, and re-dispatching onto unknown partial state turns one bad run into two.
That one still escalates to a human.

### Budget

`--retry N` (default 1). All attempts share the single `--timeout` budget, so a
two-attempt run cannot quietly take twice as long as the caller asked for. The verdict
carries an `attempts` list with each attempt's outcome, modes, and log path.

## Passing signals in

`route.py --execute` wires them through automatically. Driving the supervisor directly:

```bash
python scripts/route.py "..." --json > /tmp/decision.json
python scripts/supervise.py claude "..." --signals-file /tmp/decision.json
```

`--signals` takes the same JSON inline. Three shapes are accepted — `route.py --json` in
full, a bare `signals` block, or a raw Jev verdict — so no caller has to reshape anything.
`--no-modes` dispatches the base contract only.

## Adding modes to a card

1. Run the CLI's `--help` and find the real flags. Do not guess.
2. Add a `{flags}` token to `argv` at the position option flags belong.
3. Add the mode entries, setting `verified` honestly.
4. If a mode must swap out a base flag, list it in `replaces`.
5. Add `recovery` entries for `no_op` / `stuck` / `failed`. Only reference modes the card
   actually declares — a test enforces this.
6. `python -m unittest discover -s tests`. The card tests catch unknown mode names, a
   missing `{flags}` slot, a mode that does nothing, and recovery referencing a mode that
   is not there.
7. `python scripts/probe.py --refresh`. The registry cache is schema-versioned, so a card
   edit takes effect on the next call — but a card *shape* change needs the bump in
   `probe.REGISTRY_SCHEMA`.
