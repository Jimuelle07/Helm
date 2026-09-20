# System Design: `orca-jev` Harness

> Reads after [`idea.md`](./idea.md) (the thesis) and [`context.md`](./context.md) (what Jev is).
> This document is the build spec: layers, data shapes, the question set, thresholds, and the
> decisions we made with their reasons.

## 1. Architecture

Four layers. Each is independently testable, and the boundary between them is a plain data
structure — not a function call into a model.

```
  ┌─ L1 OBSERVE ─────────────────────────────────────────────── code only ─┐
  │  probe/hardware.py   CPU · RAM · GPU/VRAM · disk · OS                   │
  │  probe/agents.py     PATHEXT-aware CLI discovery, version, auth mode    │
  │  cards/*.json        declared competence per agent (static, reviewed)   │
  │  registry.py         probe ⨝ cards → CapabilityRegistry  (cached)       │
  └────────────────────────────────┬───────────────────────────────────────┘
                                   │ state: {task, machine, agents[]}
  ┌─ L2 JUDGE ─────────────────────▼──────────────────────────── Jev only ─┐
  │  judge/questions.py  THE question set + THE thresholds (one file)       │
  │  judge/client.py     one request, 7 questions, evaluated in parallel    │
  │  judge/fallback.py   deterministic scorer when Jev is unavailable       │
  └────────────────────────────────┬───────────────────────────────────────┘
                                   │ Verdict: typed answers + confidence
  ┌─ L3 COMPOSE ───────────────────▼──────────────────────────── code only ─┐
  │  router.py           thresholds → Route{agent, mode, gates, rationale}  │
  └────────────────────────────────┬───────────────────────────────────────┘
                                   │ Route
  ┌─ L4 ACT ───────────────────────▼───────────────────────────────────────┐
  │  executor.py         render invocation contract → run headless CLI      │
  └─────────────────────────────────────────────────────────────────────────┘
```

**Invariant:** L1 never calls a model. L2 never touches the filesystem. L3 never calls anything —
it is pure. Only L4 spawns a process. This is what makes the system cheap to test: L3 is a pure
function from `Verdict → Route`, so every threshold can be unit-tested without a network.

## 2. Language and dependencies

**Python 3.14, standard library only for the core.**

| Decision | Reason |
|---|---|
| Python over TypeScript | `context.md` leads with the Python SDK; `uv` 0.10.5 and Python 3.14.3 are already on the box; the scoring/registry logic is plain data manipulation. |
| stdlib-only core | The harness must run *today*, on a machine with no `typesafe-sdk` installed and no API key. `urllib.request` is enough for one POST. Once this ships as a **distributable skill**, this stops being a convenience and becomes the point: a skill that needs `pip install` before it works will not be run. |
| `typesafe-sdk` optional | If importable, use it; otherwise fall back to raw HTTP against the same endpoint. Identical behaviour either way. |
| JSON capability cards | Diffable, reviewable, no PyYAML dependency — and one file per agent is the natural unit for a user contributing support for a tool we have never seen. |

The core is a pure library; the CLI is a thin shell over it, so an HTTP shim or a TS port stays easy.

## 3. L1 — Observe

### 3.1 Agent discovery

Discovery must be **PATHEXT-aware** and **builtin-filtered**. From `idea.md` §4: a POSIX
`command -v` reports `cursor-agent` missing (it is `cursor-agent.cmd`) and reports `continue`
present (it is a shell builtin). Both failures corrupt the answer space, and the second is worse
than the first because it invents capability.

Resolution order per candidate binary name:
1. Try each extension in `PATHEXT` (`.COM;.EXE;.BAT;.CMD;.PS1;...`) across each `PATH` entry, plus
   the bare name (POSIX).
2. Reject anything that is not a real file on disk. *A `PATH` entry is not an installation* — the
   stale `...\Programs\Ollama` entry proves it.
3. Record the **resolved absolute path**, not the name.
4. Probe `--version` with a short timeout; a timeout marks the agent `degraded`, not `absent`.

Non-headless IDE launchers found on this machine (`kiro`, `antigravity-ide`, `cursor`, `orca`) are
recorded as `present` but carry `headless: null`, so they are **excluded from the routing answer
space** while remaining visible in `doctor`. Being installed is not the same as being routable.

### 3.2 Hardware profile

| Field | Source | Gotcha |
|---|---|---|
| CPU / cores | `Win32_Processor` / `os.cpu_count()` | — |
| RAM total + free | `Win32_OperatingSystem` | free RAM is a *point-in-time* reading; never cache it as a capability |
| GPU + VRAM | `nvidia-smi --query-gpu` **first** | `Win32_VideoController.AdapterRAM` is 32-bit and reported 4 GB for a 6141 MiB card — WMI is fallback only |
| Disk free | `shutil.disk_usage` | — |
| Local inference | Ollama binary **and** ≥1 model in `~/.ollama/models` | both required; the directory alone means nothing |

`local_inference_available` is a computed boolean, not an assumption. On this machine it is `false`,
so no route can recommend a local model.

### 3.3 Capability cards

One JSON file per agent — the **declared** knowledge that answers "an LLM does not know its own
tool unless it gets called." Shape:

```jsonc
{
  "name": "aider",
  "display_name": "Aider",
  "bin_names": ["aider"],
  "competence": "Surgical single-file or few-file edits in a git repo, with automatic commits",
  "strengths": ["precise diffs", "git-native workflow", "cheap per edit"],
  "weaknesses": ["weak at repo-wide architecture", "needs a well-specified target"],
  "task_fit": { "bugfix": 5, "test": 4, "refactor": 3, "scaffold": 2, "research": 0 },
  "context_class": "medium",
  "auth": { "mode": "api_key", "env": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"] },
  "headless": { "argv": ["aider", "--message", "{prompt}", "--yes"], "cwd_aware": true }
}
```

`competence` is the single line handed to Jev as a `Choice` criterion, so it is written *for a
judge*: concrete, discriminating, and contrastive against its siblings. Vague cards produce vague
routing — the card text is a tuned parameter, not documentation.

`task_fit` exists only for the deterministic fallback scorer (§5.3). Jev itself reads `competence`,
`strengths`, and `weaknesses`.

## 4. State handed to Jev

One object, assembled by L1, never containing secrets:

```jsonc
{
  "intent":  "<verbatim user request>",
  "repo":    { "vcs": "git", "branch": "main", "dirty": false, "languages": ["python"],
               "file_count": 42, "has_tests": true },
  "machine": { "os": "Windows 11", "cpu_cores": 8, "ram_gb": 15.7, "vram_gb": 6.0,
               "local_inference_available": false },
  "agents":  [ { "name": "claude", "competence": "...", "strengths": [], "weaknesses": [],
                 "context_class": "xlarge", "auth_mode": "subscription" } ]
}
```

Budget: 32k tokens for state + the longest question (`context.md` §4). The registry is ~7 compact
objects, so we are far inside it — but `repo` summaries must stay *summaries*. We never paste file
contents into state; that is an LLM's job, not a judge's.

## 5. L2 — Judge

### 5.1 One request, seven questions

Questions are evaluated independently and in parallel against the same state, so adding a question
costs almost nothing (`context.md` §3). We therefore fan out wide and compose in code — design
rule #2.

| Key | Type | Purpose | Consumed by |
|---|---|---|---|
| `task_kind` | Choice(10) | scaffold / feature / bugfix / refactor / test / docs / review / research / ops / other | fallback scorer, telemetry |
| `agent` | Choice(**dynamic**) | **the routing decision** — criteria built from installed agents only | router |
| `blast_radius` | Score(4) | how much damage a bad edit does | approval gate |
| `spec_clarity` | Score(4) | how well-specified the request is | clarify-vs-execute gate |
| `context_breadth` | Score(4) | one file → whole repo | agent sanity check |
| `needs_human` | Noul | requires human approval before execution | approval gate |
| `reversible` | Noul | a bad outcome is undoable with `git checkout` | approval gate |

All seven are answerable from the state alone, which is the real test of independence. `agent` does
not need to *see* `task_kind`'s answer — both read the same intent. Where a genuine information
dependency exists (e.g. re-routing *after* an agent fails), that is a **serial** second call with
the failure in state, not a second question in the same request.

### 5.2 The dynamic answer space — the keystone

```python
def agent_question(registry):
    routable = registry.routable()          # installed AND headless-capable
    criteria = {c.name: c.competence for c in routable}
    criteria["none"] = "No installed agent is a good fit; escalate to the user"
    return Choice(instructions="Which installed agent should execute this task", criteria=criteria)
```

Because `criteria` is built from probe output, **recommending an uninstalled agent is structurally
impossible** rather than merely unlikely (`idea.md` §3, Claim 3). The `none` escape hatch is
mandatory per `context.md` §3 — without it a bad taxonomy forces a bad answer.

### 5.3 Fallback when Jev is unavailable

No `TYPESAFE_API_KEY` is set on this machine, so this path is **load-bearing, not decorative**.

`judge/fallback.py` scores each routable agent with `task_fit[task_kind] × context_class_match`,
using keyword heuristics for `task_kind`. It returns the same `Verdict` shape with
`source="fallback"` and **`confidence` capped at 0.5**, which — by the thresholds in §6 —
automatically forces every fallback route into recommend-only mode rather than auto-execution.

That cap is the design: a degraded brain is allowed to *suggest*, never to *act unattended*. Every
surface states which brain answered.

## 6. Thresholds

All thresholds live beside the questions in `judge/questions.py`, as `context.md` design rule #7
requires — one reviewable, diffable surface.

| Name | Value | Meaning |
|---|---|---|
| `AUTO_ROUTE_MIN_CONFIDENCE` | 0.75 | below → recommend, do not auto-execute |
| `CLARIFY_MAX_SPEC_CLARITY` | 1.0 | below → ask the user to clarify first |
| `HUMAN_GATE_NOUL` | 0.50 | `needs_human` above this → require approval |
| `AUTO_MAX_BLAST_RADIUS` | 2.0 | above → require approval |
| `IRREVERSIBLE_NOUL` | 0.40 | `reversible` below this → require approval |
| `FALLBACK_CONFIDENCE_CAP` | 0.50 | ceiling on any non-Jev verdict |

> **Every one of these is UNCALIBRATED.** They are starting points, not tuned values.
> `context.md` §5 is explicit: calibration is a property of a *population* of recorded traces, and a
> threshold must be fitted against real ones. Until `traces/` has enough rows to fit against, these
> numbers are honest guesses and are labelled as such in the code.

Gate composition is **conjunctive and fail-closed** — every gate must pass for `mode="auto"`:

```python
auto_ok = (confidence >= 0.75 and needs_human < 0.50
           and blast_radius <= 2.0 and reversible >= 0.40
           and spec_clarity > 1.0 and source == "jev")
```

## 7. L4 — Act

The chosen card's `headless.argv` is rendered with the prompt and run via `subprocess.run` with
`shell=False` (list argv, never a string — the prompt is untrusted text and must never reach a
shell parser). Default mode is `recommend`: print the exact command and the reasoning, run nothing.
`--execute` opts in, and still refuses when any gate in §6 fails.

Verified headless contracts are stored per card and confirmed against `--help` at build time rather
than assumed, because a wrong flag is a silent routing failure.

## 8. Telemetry for calibration

Every decision appends one JSONL row to `traces/decisions.jsonl`: state digest, all seven raw
answers with confidences, the composed route, the source (`jev` | `fallback`), and — when known —
the outcome. This is the corpus §6's thresholds get fitted against. Without it, calibration is
impossible and the thresholds stay guesses forever, so the trace writer ships in v1, not later.

## 9. Pin the model

Use `jev-1.13.0`, never `jev-latest`. Thresholds calibrated against one model version are
invalidated by a silent alias shift (`context.md` §9). The pin lives next to the thresholds it
protects.

## 10. Packaging: this ships as an agent skill

The harness is not just for the machine it was written on, so it is packaged as a
**distributable agent skill** rather than a local project. That constraint pushed the design
in three directions worth recording:

- **Zero install.** stdlib-only, two scripts, no build step. A skill with a setup ritual is a
  skill nobody runs.
- **Two layers collapsed into two files.** The abstract design has four layers; the shipped
  skill has `probe.py` (L1) and `route.py` (L2+L3+L4). The layer *boundaries* survive as
  function boundaries — `compose()` is still pure and still independently testable — but a
  skill that spreads eight modules across a package is harder for a reader to audit than two
  files they can read end to end. Auditability beat purity here.
- **Progressive disclosure.** `SKILL.md` carries the workflow and stays short. The detail
  lives in `references/`, loaded only when the task needs it.

```
skills/agent-router/
├── SKILL.md                        # workflow + how to present a recommendation
├── scripts/
│   ├── probe.py                    # L1: observe (hardware + PATHEXT-aware discovery)
│   ├── route.py                    # L2-L4: questions, thresholds, compose, execute
│   └── cards/*.json                # 13 capability cards (7 verified, 6 portability)
└── references/
    ├── capability-cards.md         # schema; how to write a discriminating competence line
    ├── jev-decision-layer.md       # the question set and the Jev API
    └── calibration.md              # thresholds, why they are guesses, how to fit them
tests/test_router.py                # 31 tests over the pure layers
docs/machine-profile.md             # generated snapshot of the development box
```

### Build order (completed)

1. `cards/` — the declared knowledge, written before any code that consumes it
2. `probe.py` — hardware + PATHEXT-aware discovery *(the bug-prone part)*
3. `route.py` — question set, thresholds, Jev client, fallback, compose, execute
4. `tests/` — pure layers exhaustively
5. `SKILL.md` + `references/` — the distributable surface

### What the build actually changed about the design

Three things the plan got wrong, corrected in code and worth carrying forward:

- **Auth was going to be a routing gate.** It is not. Both `aider` and `cursor-agent` on
  this machine report no discoverable credentials and both work fine — tokens live in
  keychains and browser sessions a probe cannot enumerate. Gating on undetected credentials
  would have silently dropped two working agents, which is the *same* failure as a
  PATHEXT-blind probe, just wearing a more reassuring error message. Auth is now a caveat.
- **`probabilities` meant two different things.** Jev returns a real distribution; the
  fallback was writing raw scores under the same key. Normalised, because a field whose
  scale depends on which brain answered is unanalysable the moment anyone tries to calibrate.
- **Windows batch execution is an injection surface.** Most agent CLIs on Windows resolve to
  `.cmd` shims, which `CreateProcess` runs through `cmd.exe` — so an untrusted prompt
  containing shell metacharacters is the BatBadBut class of bug (CVE-2024-24576). The
  executor refuses rather than attempting to quote, since `cmd.exe` quoting is genuinely
  hard to get right and the user can always run the printed command themselves.
- **The Jev key can't be a machine-level setting once this is a shared skill.** The original
  plan treated `TYPESAFE_API_KEY` as an environment variable the harness reads — fine for
  one developer's machine, wrong for a skill teammates install independently. Each person
  has their own TypeSafe account, so `keystore.py` resolves the key from the environment
  first, then a locally stored per-user file written by `route.py --set-api-key`, at the
  same trust boundary as `~/.netrc`. Setting a key must also take effect immediately rather
  than waiting out the 24h registry cache — `probe_jev()` is deliberately excluded from what
  `load_cached()` caches, for the same reason free RAM already was.
