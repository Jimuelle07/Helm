<h1 align="center">Helm</h1>

<p align="center">
  <strong>Steering and control for the coding agents already on your machine.</strong>
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-FFD43B?style=plastic&logo=python&logoColor=1F2937&labelColor=3776AB">
  <img alt="Standard library only" src="https://img.shields.io/badge/Dependencies-stdlib%20only-34D399?style=plastic&logo=dependabot&logoColor=white&labelColor=065F46">
  <img alt="Jev optional" src="https://img.shields.io/badge/Jev-optional-C084FC?style=plastic&logo=sparkles&logoColor=white&labelColor=6D28D9">
  <img alt="Cross platform" src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-93C5FD?style=plastic&logo=windows&logoColor=white&labelColor=1E3A8A">
  <img alt="MIT license" src="https://img.shields.io/badge/License-MIT-5EEAD4?style=plastic&logo=opensourceinitiative&logoColor=white&labelColor=0F766E">
</p>

> Not related to Helm, the Kubernetes package manager. This Helm steers AI
> coding agents.

Helm discovers which coding-agent CLIs are installed and authenticated, routes
work to the best available one, and can supervise the result so a clean exit code
does not get mistaken for finished work.

If you have `claude`, `codex`, `cursor-agent`, `gemini`, `aider`, `opencode`,
`copilot`, or local agents on your machine, they are usually invisible to one
another. An LLM only knows the tools listed in its prompt. Helm gives the
orchestrator a live inventory before it recommends, delegates, or supervises.

## At A Glance

| Script | Job | Uses Jev? |
| --- | --- | --- |
| `skills/helm/scripts/probe.py` | Finds installed agents, auth state, hardware, and fixes | No |
| `skills/helm/scripts/route.py` | Chooses the best available agent for a task | Optional |
| `skills/helm/scripts/supervise.py` | Dispatches a worker and judges whether it finished | Optional |

[TypeSafe Jev](https://typesafe.ai) is a System One model: it emits typed
decisions instead of text. Helm uses it for low-latency, low-cost routing and
completion judgement. Without a TypeSafe key, Helm falls back to a deterministic
local scorer that can recommend but cannot act unattended.

## Quick Start

No package install is required. Helm uses Python 3.10+ and the standard library.

```bash
python skills/helm/scripts/probe.py --repo .
python skills/helm/scripts/route.py "add retry logic to the payment client" --repo .
```

Enable live Jev judgement once per user:

```bash
python skills/helm/scripts/route.py --set-api-key apikey_...
python skills/helm/scripts/probe.py --check-jev
```

The key is stored locally at `~/.cache/helm/credentials.json`. You can also set
`TYPESAFE_API_KEY` for CI or temporary use.

## What Helm Solves

### 1. Recommending tools you do not have

The options handed to Jev are built from what `probe.py` actually found. Jev
cannot return a value outside its declared answer space, so suggesting an
uninstalled agent is structurally impossible.

### 2. Routing to a CLI that is not logged in

Installing an agent and authenticating it are separate steps. Many CLIs pass a
`--version` check but fail on the first model call. Helm asks each tool for its
own status and removes explicitly logged-out agents from routing:

```text
INSTALLED BUT NOT AUTHENTICATED (1)  <- log in to use these
  codex          Not logged in.
                 fix: codex login
```

The rule is intentionally asymmetric. "We could not find a credential" does not
block, because credentials may live in keychains the probe cannot read. Only
"the tool said it is logged out" blocks routing.

### 3. Checking completion without burning orchestration context

Worker transcripts can be tens of thousands of tokens. Helm can ask Jev to read
the transcript and return a compact completion judgement:

```text
DONE -- codex (completed)
  Edited src/parser.py and ran the suite: 14 passed
  exit 0 after 47.2s | judged by jev
  next: accept
  full transcript: /tmp/helm-runs/codex-1758.log
```

This catches cases an exit code cannot: agents that exit `0` after doing no
work, or agents that exit `0` after asking a question into a headless run.

## Skill Layout

Helm is packaged like a Codex skill. The skill instructions explain when to use
the harness, while the scripts and references provide the runnable pieces.

```text
.
|-- SKILL.md
|-- skills/helm/scripts/
|   |-- probe.py
|   |-- route.py
|   |-- supervise.py
|   `-- cards/
`-- skills/helm/references/
    |-- walkthrough.md
    |-- capability-cards.md
    |-- jev-decision-layer.md
    `-- calibration.md
```

## Teaching Helm A New Agent

Capability cards in `skills/helm/scripts/cards/*.json` are the declared knowledge that lets
Helm reason about an agent without invoking it. Add one file to support a new
CLI:

```jsonc
{
  "name": "mytool",
  "bin_names": ["mytool"],
  "competence": "Short, contrastive description of what this tool is uniquely good at",
  "task_fit": { "bugfix": 5, "refactor": 2, "...": 0 },
  "context_class": "medium",
  "headless": { "argv": ["mytool", "-p", "{prompt}"] },
  "auth_check": { "argv": ["mytool", "status"], "ok_pattern": "logged in" }
}
```

The `competence` line is what Jev reads, so treat it as a routing parameter, not
generic documentation. Write it to distinguish the tool from the other cards.
See `skills/helm/references/capability-cards.md`.

## Documentation

| File | Purpose |
| --- | --- |
| `skills/helm/references/walkthrough.md` | Start here: install-to-end-to-end walkthrough with real output |
| `SKILL.md` | Skill workflow and result-presentation rules |
| `skills/helm/references/capability-cards.md` | Card schema and guidance for writing routing-friendly competence lines |
| `skills/helm/references/jev-decision-layer.md` | Every decision question Helm asks Jev, and why each is independent |
| `skills/helm/references/calibration.md` | Thresholds, assumptions, and how to fit them later |

## Honest Limitations

- Every threshold is uncalibrated. They are conservative starting points and
  decisions log to `~/.cache/helm/decisions.jsonl` so they can be fitted later.
- The Jev wire contract is verified as of 2026-09-20 with `jev-1.13.0`: Bearer
  auth, all three question types, and a model pin the API validates.
- `GET /v1/models` lists only the `jev-latest` and `jev-preview` aliases, so the
  concrete version cannot be discovered from that endpoint. Traces record the
  resolved version so drift is detectable.
- Some capability cards are best effort. Cards whose invocation or auth commands
  have not been verified are flagged at runtime.
