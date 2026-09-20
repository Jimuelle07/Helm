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
    "argv": ["aider", "--message", "{prompt}", "--yes-always"],
    "model_flag": "--model",
    "cwd_aware": true
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

Set `contract_verified: true` only after confirming the flags against the tool's own
`--help`. A wrong flag is a silent routing failure: the router looks like it worked and the
agent never runs. Unverified cards are still usable — they are surfaced as a caveat and
carry a small scoring penalty.

Verified contracts as of this writing:

| Agent | Headless invocation |
|---|---|
| `claude` | `claude -p "<prompt>"` |
| `codex` | `codex exec "<prompt>"` |
| `cursor-agent` | `cursor-agent -p "<prompt>"` |
| `gemini` | `gemini -p "<prompt>"` |
| `aider` | `aider --message "<prompt>" --yes-always` |
| `opencode` | `opencode run "<prompt>"` |
| `copilot` | `copilot -p "<prompt>"` |

## Adding an agent

1. Copy the closest existing card and edit it.
2. Confirm the headless invocation against `--help`; set `contract_verified` honestly.
3. Write a `competence` line that contrasts with every existing card.
4. Fill in all ten `task_fit` values.
5. Run `python -m unittest discover -s tests` — the card tests will catch a missing field,
   a bad `context_class`, or a duplicated competence line.
6. Re-probe with `--refresh`, since the registry caches for 24 hours.

## A note on `auth`

The probe checks whether the named env vars are set and whether the config paths exist. It
never reads their contents, and nothing about credentials ever enters the state sent to
Jev.

Undetected credentials do **not** disqualify an agent. Many CLIs store tokens in an OS
keychain or a browser-managed session that no reasonable probe can enumerate, so treating
"not found" as "not present" would quietly drop working agents out of the answer space —
the same silent-shrinkage failure as a probe that cannot see `.CMD` files. It is reported
as a caveat instead.
