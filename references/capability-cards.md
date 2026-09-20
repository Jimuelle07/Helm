# Capability Cards

A card is the declared, machine-readable answer to "what is this agent good at?" — the
knowledge that lets the router choose without invoking anything first. One JSON file per
agent in `scripts/cards/`, named after the agent.

## Schema

```jsonc
{
  "name": "aider",                    // required, matches the filename
  "display_name": "Aider",            // required, shown to the user
  "bin_names": ["aider"],             // required, candidates for shutil.which()
  "competence": "Surgical edits to one or a few named files, committed to git automatically; needs the target to be identified up front",
  "strengths": ["precise minimal diffs", "git-native with automatic commits"],
  "weaknesses": ["will not explore a repo to find its own target"],
  "task_fit": {                       // required: 0-5 for EVERY task kind
    "scaffold": 2, "feature": 3, "bugfix": 5, "refactor": 3, "test": 4,
    "docs": 3, "review": 2, "research": 0, "ops": 1, "other": 2
  },
  "context_class": "medium",          // small | medium | large | xlarge
  "requires": {                       // optional hard constraints
    "local_inference": true,
    "min_vram_gb": 6.0
  },
  "auth": {
    "modes": ["api_key"],             // api_key | subscription | oauth | none
    "env": ["OPENAI_API_KEY"],        // presence is checked; values are NEVER read
    "config": ["~/.aider.conf.yml"]
  },
  "headless": {
    "argv": ["aider", "{flags}", "--message", "{prompt}", "--yes-always"],
    "model_flag": "--model",
    "cwd_aware": true,
    "modes": {                        // optional: see references/dispatch-modes.md
      "plan": { "args": ["--architect"], "verified": true },
      "unattended": { "satisfied_by_base": true, "verified": true }
    },
    "recovery": {                     // optional: the agent's own cure per outcome
      "failed": { "cleanup": ["aider", "--message", "/undo", "--yes-always"],
                  "prompt_prefix": "A previous attempt failed and was rolled back. " }
    }
  },
  "list_models": ["aider", "--list-models"],   // optional
  "contract_verified": true           // have you actually confirmed argv against --help?
}
```

`task_fit` must contain all ten kinds — a test enforces this, because a missing key
silently defaults the agent to mediocre in the fallback scorer rather than failing loudly.

## Writing a good `competence` line

This single line is what Jev reads as the `Choice` criterion. It is the most load-bearing
string in the whole system: vague cards produce vague routing. Treat it as a tuned
parameter, not as documentation.

**Be contrastive.** The line's job is to distinguish this agent from its siblings, not to
describe it in isolation. If two cards could plausibly swap lines, neither is doing its job
— and a test asserts they are at least not identical.

**Name the situation, not the quality.** "Good at refactoring" tells a judge nothing that
four other cards do not also claim. "Edits that first require finding every call site
across an unfamiliar repository, using a semantic index rather than grep" describes a
recognisable situation a task either matches or does not.

**Include the precondition.** Aider's line ends with "needs the target to be identified up
front" — that is what stops it being chosen for exploratory work, and it does more routing
work than any of its strengths.

| Weak | Strong |
|---|---|
| "Powerful AI coding assistant" | "Long-horizon multi-file work where the plan itself has to be worked out before any code is written" |
| "Fast and cheap" | "Surgical edits to one or a few named files, committed to git automatically" |
| "Great for large codebases" | "Very large context sweeps: reading or auditing whole directories in a single pass" |

## `context_class`

Rough size of job the agent comfortably holds at once. The fallback scorer penalises an
agent whose class is *smaller* than the task needs much more heavily than one that is
larger, because being under-resourced fails the task while being over-resourced merely
costs more.

| Class | Fits |
|---|---|
| `small` | One file, or a single focused question |
| `medium` | A handful of related files in one module |
| `large` | Many files across several modules |
| `xlarge` | Whole repository, or knowledge spanning more than one repo |

## `headless` and `contract_verified`

`argv` is the exact command to run the agent non-interactively. The token `{prompt}` is
replaced with the task text as a **single argv element** — never string-concatenated, so
quotes and ampersands in a prompt cannot break out.

The token `{flags}` is the insertion point for flags the dispatcher injects from Jev's
metrics — a sandbox flag for a high-blast change, an unattended flag when nobody is
watching. Unlike `{prompt}` it *splices*, because it only ever holds card-authored
constants. It is required on any card that declares flag-based `modes`; without it there is
no non-arbitrary place to put them, and the planner refuses to guess. `argv` must remain a
working headless invocation on its own, with `{flags}` expanding to nothing — that is what
makes the whole layer additive. See `references/dispatch-modes.md`.

Set `contract_verified: true` only after confirming the flags against the tool's own
`--help`. A wrong flag is a silent routing failure: the router looks like it worked and the
agent never runs. Unverified cards are still usable — they are surfaced as a caveat and
carry a small scoring penalty.

Verified contracts as of this writing:

| Agent | Headless invocation | Mode flags verified |
|---|---|---|
| `claude` | `claude -p "<prompt>"` | yes |
| `codex` | `codex exec "<prompt>"` | yes |
| `gemini` | `gemini -p "<prompt>"` | yes |
| `aider` | `aider --message "<prompt>" --yes-always` | yes |
| `opencode` | `opencode run "<prompt>"` | yes |
| `copilot` | `copilot -p "<prompt>"` | yes |
| `cursor-agent` | `cursor-agent -p "<prompt>"` | no — not installed when written |

Mode flags for the other agents come from the *CLI Coding Agents Cheat Sheet* and carry
`"verified": false` plus a `source`, because a document is not the binary — the sheet
describes a `--approval-mode` that the installed `codex exec` does not have, and a Copilot
CLI that is a different tool from the one on this machine. See
`references/dispatch-modes.md` for the provenance table and both discrepancies.

## `auth_check` -- proving the tool can actually run

Installing a CLI and authenticating it are separate acts. A tool that was never logged in
passes a `--version` probe and then fails on its first model call, which surfaces as a
confusing runtime error instead of a clear precondition failure. `auth_check` lets the
probe ask the tool itself:

```jsonc
"auth_check": {
  "argv": ["codex", "login", "status"],   // argv[0] is replaced with the resolved path
  "ok_pattern": "logged in",               // regex, case-insensitive
  "fail_pattern": "not logged in",         // tested FIRST -- see below
  "timeout": 30
}
```

Resolution order, and the reasoning behind it:

1. **`fail_pattern` is tested before `ok_pattern`.** A CLI can exit 0 while printing "not
   logged in", and `"not logged in"` also contains `"logged in"`. The explicit negative has
   to win, or a substring match silently inverts the verdict.
2. **`ok_pattern` match** -> `authenticated`.
3. **`ok_pattern` declared but nothing matched** -> `unknown`, *not* authenticated. The
   tool's output format may simply have changed in a new version, and assuming "fine" there
   is how you route to a dead agent.
4. **No patterns declared** -> exit code decides.
5. **Timeout, crash, or no `auth_check` at all** -> `unknown`.

Only state 2's opposite -- a definitive `unauthenticated` -- removes an agent from routing.
Everything ambiguous stays routable with a caveat, because a check that *cannot answer* must
not be allowed to condemn a working tool.

Verified auth commands:

| Agent | Command | Success signal |
|---|---|---|
| `claude` | `claude auth status` | JSON `"loggedIn": true` |
| `codex` | `codex login status` | `Logged in using ChatGPT` |
| `cursor-agent` | `cursor-agent status` | `Logged in as ...` |
| `opencode` | `opencode auth list` | `N credentials` (`0 credentials` = logged out) |

`gemini`, `aider` and `copilot` have no read-only status command I could verify, so they
carry no `auth_check` and stay `unknown`. That is the correct outcome: unknown never blocks.

Add a `LOGIN_HINTS` entry in `probe.py` alongside any new `auth_check` -- a diagnosis is
only useful with the remedy attached.

## Adding an agent

1. Copy the closest existing card and edit it.
2. Confirm the headless invocation against `--help`; set `contract_verified` honestly.
3. Write a `competence` line that contrasts with every existing card.
4. Fill in all ten `task_fit` values.
5. Add an `auth_check` and a `LOGIN_HINTS` entry if the CLI has a status command
6. Add a `{flags}` slot and a `modes` table so Jev's metrics can reach this agent's own
   flags — see `references/dispatch-modes.md`. A card without one still routes fine; it
   simply reports `unavailable: sandbox` and similar when a rule asks for something it
   cannot express.
7. Run `python -m unittest discover -s tests` — the card tests will catch a missing field,
   a bad `context_class`, a duplicated competence line, an unknown mode name, a mode that
   does nothing, or recovery referencing a mode the card does not declare.
8. Re-probe with `--refresh`, since the registry caches for 24 hours.

## A note on `auth`

The probe checks whether the named env vars are set and whether the config paths exist. It
never reads their contents, and nothing about credentials ever enters the state sent to
Jev.

Undetected credentials do **not** disqualify an agent. Many CLIs store tokens in an OS
keychain or a browser-managed session that no reasonable probe can enumerate, so treating
"not found" as "not present" would quietly drop working agents out of the answer space —
the same silent-shrinkage failure as a probe that cannot see `.CMD` files. It is reported
as a caveat instead.
