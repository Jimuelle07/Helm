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
| `plan` | Settle the architecture before writing code | Aider `--architect`, Qwen `--mode plan-act`, Amp's "Consult the Oracle" |
| `parallel` | Fan out across sub-agents instead of walking files serially | Claude's "Fan out subagents in parallel to …", Amp's "Dispatch subagents", Codex's "Map-reduce repository review" |
| `readonly` | Forbid writes outright | Claude/Gemini `plan` approval mode, Cursor `--mode=plan`, Goose `/plan`, Codex `--sandbox read-only`, Aider `--dry-run` |

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

### Enforced vs advisory

A mode is **enforced** when the tool guarantees it — a flag the CLI parses, or a base
contract that already covers it. It is **advisory** when all we did was ask nicely, which
includes slash directives: `/plan` is only as reliable as the agent's own handling of it,
and nothing here can check that.

For `plan` and `parallel` the difference is a quality question. For `readonly` and
`sandbox` it is a safety question, so those get said out loud:

```
readonly is ADVISORY on goose -- the agent is instructed, not prevented.
Do not treat it as containment
```

The same `review` task, three agents:

| Agent | `readonly` becomes | Enforced? |
|---|---|---|
| `gemini` | `--approval-mode plan` | yes — cannot write |
| `cursor-agent` | `--mode=plan` | yes (flag unverified here) |
| `goose` | `/plan ` prefix | no — a directive it may ignore |
| `opencode`, `crush` | "Do not modify …" prefix | no — plain instruction |

Nothing declares this; it is derived from the entry's shape, so no card can get it wrong.

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
| `source` | Where the entry came from — a `--help` reading or the cheat sheet |
| `note` | Shown to the user in the dispatch notes |

`args` and `satisfied_by_base` make a mode *enforced*; a `prompt_prefix` alone makes it *advisory*. See above.

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

### `verified` and `source`

Same discipline as `contract_verified`, and just as load-bearing. A wrong flag is not a
degraded run, it is a dead one.

- **`source`** records where the entry came from. Every mode has one.
- **`verified: true`** is reserved for flags read off that CLI's own `--help` on a machine
  where it was installed. A test enforces the implication: a mode with `args` may only
  claim `verified` when its `source` mentions `--help`.

| Source | Agents | Trust |
|---|---|---|
| `--help`, verified on this machine | claude, codex, gemini, aider, opencode, copilot | flags confirmed against the binary |
| *CLI Coding Agents Cheat Sheet* | cursor-agent, crush, goose, amp, qwen, droid | documented, not confirmed here |

A cheat sheet is a much better source than a guess — it corrected two cards and fixed a
real bug, below — but it is not the binary, and this repo has already caught it describing
flags the installed CLI does not have. Unverified modes surface as a caveat:

```
caveat: injected flags for readonly are unverified against this CLI's --help
note:   readonly: flags not confirmed against this CLI's --help (source: CLI Coding Agents Cheat Sheet)
```

### Where the sheet and the binary disagree

Two, and the installed binary wins both times, because it is the thing that will run.

**Codex.** The sheet lists `--approval-mode <auto|suggest|manual>` and the slash commands
`/diff`, `/apply`, `/rollback`. The installed `codex exec` has none of them; it has
`-s/--sandbox <read-only|workspace-write|danger-full-access>` and `--approve-for-me`. The
card uses `--sandbox`.

**Copilot.** The sheet's section documents `gh copilot suggest` / `gh copilot explain` —
the old `gh` extension, a shell-command suggester with `-t shell|git|gh`. The installed
binary is **GitHub Copilot CLI 1.0.83**, a different tool entirely: `-p/--prompt`,
`--allow-all-tools`, `--mode plan`. The card targets the standalone CLI, which is what
`bin_names: ["copilot"]` resolves to.

Where a card's situation differs from the sheet like this, it carries a `card_note`.

### The bug the sheet caught

Goose's `/plan` **locks the agent into a read-only architectural phase until `/endplan`**.
The first draft of these cards used `/plan` for the `plan` mode — architecture-first, then
build. In a one-shot `goose run` there is no second turn to send `/endplan` from, so every
scaffold task routed to Goose would have planned, written nothing, and exited clean: a
guaranteed `no_op`, and one that only the semantic judge would have caught.

`/plan` is therefore mapped to **`readonly`**, where a read-only lock is the entire point.
Goose's `plan` mode uses a plain architecture-first prefix instead.

This is the general shape of the risk: a directive that is correct interactively can be
exactly wrong headless, because the turn that would release it never comes. The same
reasoning applies to Aider's `--architect`, which proposes a plan and then waits for
acceptance — the card pairs it with `--auto-accept-architect` for exactly this reason — and
to Gemini's `/reset`, which is pointless headless because `-p` starts a fresh session every
run anyway.

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

1. Run the CLI's `--help` and find the real flags. Check the cheat sheet too, but when
   they disagree the binary wins — see "Where the sheet and the binary disagree".
2. Add a `{flags}` token to `argv` at the position option flags belong.
3. Add the mode entries. Set `source` always, and `verified` only for flags you read off
   `--help` yourself.
4. If a mode must swap out a base flag, list it in `replaces`.
5. Add `recovery` entries for `no_op` / `stuck` / `failed`. Only reference modes the card
   actually declares — a test enforces this.
6. `python -m unittest discover -s tests`. The card tests catch unknown mode names, a
   missing `{flags}` slot, a mode that does nothing, and recovery referencing a mode that
   is not there, a mode with no `source`, and a `verified` flag that no `--help` backs.
7. `python scripts/probe.py --refresh`. The registry cache is schema-versioned, so a card
   edit takes effect on the next call — but a card *shape* change needs the bump in
   `probe.REGISTRY_SCHEMA`.
