<h1 align="center">Helm</h1>

<p align="center">
  <strong>Steering and control for the coding agents already on your machine.</strong>
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-FFD43B?style=plastic&logo=python&logoColor=1F2937&labelColor=3776AB">
  <img alt="Standard library only" src="https://img.shields.io/badge/Dependencies-stdlib%20only-34D399?style=plastic&logo=dependabot&logoColor=white&labelColor=065F46">
  <img alt="Powered by TypeSafe Jev" src="https://img.shields.io/badge/Decisions-TypeSafe%20Jev-C084FC?style=plastic&logo=sparkles&logoColor=white&labelColor=6D28D9">
  <img alt="Cross platform" src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-93C5FD?style=plastic&logo=windows&logoColor=white&labelColor=1E3A8A">
  <img alt="MIT license" src="https://img.shields.io/badge/License-MIT-5EEAD4?style=plastic&logo=opensourceinitiative&logoColor=white&labelColor=0F766E">
  <img alt="Claude Code plugin" src="https://img.shields.io/badge/Install-Claude%20Code%20plugin-F59E0B?style=plastic&logo=anthropic&logoColor=white&labelColor=92400E">
</p>

> Not related to Helm, the Kubernetes package manager. This Helm steers AI
> coding agents.

Helm is an orchestrator for coding agents. It discovers which agent CLIs are
installed and authenticated on your machine, routes each task to the best one,
dispatches it, and judges whether the work actually got done.

It ships as a Claude Code plugin containing a single skill — two commands to
install, and the agent you are already talking to gains an inventory of every
sibling agent on the box. See [Install The Plugin](#install-the-plugin).

If you have `claude`, `codex`, `cursor-agent`, `gemini`, `aider`, `opencode`,
`copilot`, or local agents installed, they are usually invisible to one another.
An LLM only knows the tools listed in its prompt. Helm gives the orchestrator a
live inventory — and then makes the routing call for it.

**The probe observes. Jev judges. The chosen agent generates.**

That middle step is what makes the rest work. Orchestration is mostly
*judgement* — which agent, how risky, is it finished — and judgement is the one
thing you cannot afford to spend a frontier model on for every task. Helm sends
it to [TypeSafe Jev](https://typesafe.ai) instead.

## At A Glance

| Script | Job | Role |
| --- | --- | --- |
| `skills/helm/scripts/probe.py` | Finds installed agents, auth state, hardware, and fixes | Observes |
| `skills/helm/scripts/route.py` | Chooses the best available agent for a task | Asks Jev |
| `skills/helm/scripts/supervise.py` | Dispatches a worker and judges whether it finished | Asks Jev |

## The Decision Layer

[TypeSafe Jev](https://typesafe.ai) is a **System One** model. It is
non-generative — it emits no tokens, no prose, no code. You hand it state plus
typed questions and it returns typed probabilistic decisions in a single forward
pass.

> Unstructured state in → typed probabilistic decisions out.
>
> **Code calculates. Jev judges. LLMs reason and generate.**

Helm asks it seven questions per routing call, evaluated **independently and in
parallel** in one request:

| Question | Type | What it decides |
| --- | --- | --- |
| `agent` | Choice (**dynamic**) | Which installed agent takes this task |
| `task_kind` | Choice (10) | scaffold / feature / bugfix / refactor / test / docs / … |
| `blast_radius` | Score (4) | How much damage a wrong edit does |
| `spec_clarity` | Score (4) | Is this specified enough to hand off at all? |
| `context_breadth` | Score (4) | How much of the codebase must be understood |
| `needs_human` | Noul | Should a person approve this before it runs? |
| `reversible` | Noul | Is `git checkout` a sufficient undo? |

A real call against `jev-1.13.0` — the seven-question routing decision above —
measured **0.7 s and 2,149 input tokens, or $0.00009**. Input is $0.042 per
million tokens and output is billed at zero, so the decision layer costs about
a hundredth of a cent per task.

### Why a typed decision model and not another LLM

**The answer space is built from what the probe found.** `agent` is a `Choice`
whose options are constructed at call time from the agents actually installed:

```python
criteria = {a["name"]: a["competence"] for a in routable}
criteria["none"] = "No installed agent is a good fit; escalate to the user"
```

Jev cannot return a value outside that space. So *"recommend the best model
available on this machine"* stops being an instruction you hope a model respects
and becomes **a property of the schema** — suggesting an agent you do not have
is structurally impossible, not merely unlikely.

**It is cheap enough to run on everything.** At $0.042 per million input tokens,
the control plane can judge *every* task rather than only the ones that look
hard — which is the condition under which routing starts paying for itself.

**It keeps transcripts out of your context.** Asking "did the worker finish?" by
having the orchestrating model read a 50k-token agent transcript is the single
most expensive thing in the loop, and it permanently fills that model's context
with build noise. Jev reads it for about a fifth of a cent and returns a few
hundred bytes. The transcript goes to disk; the orchestrator gets the verdict and
a path.

**Confidence is a real signal.** Jev returns a probability distribution, not a
pick. When two agents are genuinely a close call, confidence drops, a gate fires,
and Helm recommends instead of running. That is the system working.

### Jev is required

Every routing choice, blast-radius estimate and completion judgement comes from
Jev — there is no second scorer behind it. Without a key, `route.py` and
`supervise.py` exit with setup instructions rather than producing a weaker answer
you might mistake for a real one. `probe.py` still prints its inventory, then
exits non-zero.

This is deliberate. The judgements Helm makes are exactly the ones local
heuristics are bad at: telling "wrote the code" from "wrote *about* the code"
needs a reader, not a keyword scan. A fallback that cannot tell should not be
quietly answering. See
[`jev-decision-layer.md`](skills/helm/references/jev-decision-layer.md).

## Quick Start

No package install is required. Helm uses Python 3.10+ and the standard library.
The commands below drive the scripts directly from a clone; to install it as a
plugin instead, see [Install The Plugin](#install-the-plugin).

Configure the decision layer first — nothing routes without it. This is once per
user, because the skill is shared across a team and each person brings their own
TypeSafe account:

```bash
python skills/helm/scripts/route.py --set-api-key apikey_...
python skills/helm/scripts/probe.py --check-jev
```

Then use it:

```bash
python skills/helm/scripts/probe.py --repo .
python skills/helm/scripts/route.py "add retry logic to the payment client" --repo .
```

The key is stored locally at `~/.cache/helm/credentials.json`. You can also set
`TYPESAFE_API_KEY` for CI or temporary use.

`probe.py --check-jev` is the one command worth running after setup: it makes a
real inference call, so it catches a key that lists models fine but fails at
routing time. Exit `0` means Helm can route.

## A Decision, End To End

```bash
$ python skills/helm/scripts/route.py \
    "rename the Client class to ApiClient across the entire repository and update every call site"
```

```text
RECOMMEND -> Cursor Agent (cursor-agent)
  why: Edits that first require finding every call site or usage across an
       unfamiliar repository, using a semantic index rather than grep

  command:
    cursor-agent -p Use your repository index to find every affected call site
    first, then apply the change across all of them in one pass rather than file
    by file. Task: rename the Client class to ApiClient across the entire ...

  modes injected: parallel

  signals: kind refactor | confidence 0.97 | blast 1.98 | clarity 1.71 |
           breadth 2.68 | needs_human 0.62 | reversible 0.58
  judged by: jev
  alternative: claude (0.02) -- Long-horizon multi-file work: architecture ...

  not auto-executing because:
    - needs_human 0.62 > 0.5
```

Every number on that `signals` line did something:

| Signal | Value | What it caused |
| --- | --- | --- |
| `agent` | cursor-agent @ 0.97 | Picked the semantic indexer over Claude (0.02) — not a close call |
| `context_breadth` | 2.68 | Repo-wide, so `parallel` mode was injected into the command |
| `task_kind` | refactor | Selected the find-all-call-sites-first preamble |
| `needs_human` | 0.62 | Crossed the 0.5 gate → `RECOMMEND`, not auto-execute |
| `blast_radius` | 1.98 | Under the 2.0 ceiling, so this gate stayed quiet |

One Jev request produced all of it, in about a second, for roughly $0.0001. The
prompt handed to `cursor-agent` is not generic — it was assembled from Jev's
metrics before it was printed. See
[`dispatch-modes.md`](skills/helm/references/dispatch-modes.md) for how a metric
becomes a CLI flag.

## What Helm Solves

### 1. Recommending tools you do not have

The options handed to Jev are built from what `probe.py` actually found, so
suggesting an uninstalled agent is structurally impossible rather than merely
unlikely — see [The Decision Layer](#the-decision-layer). This is the single
reason to use a typed decision model here instead of asking an LLM to please
only recommend things you have.

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

This is what makes multi-step orchestration affordable at all. Worker transcripts
run to tens of thousands of tokens, and the orchestrating model reading one costs
real money *and* permanently pollutes its context with build noise it carries for
the rest of the session. Jev reads it instead and returns a few hundred bytes:

```text
DONE -- codex (completed)
  Edited src/parser.py and ran the suite: 14 passed
  exit 0 after 47.2s | judged by jev
  next: accept
  full transcript: /tmp/helm-runs/codex-1758.log
```

This catches cases an exit code cannot: agents that exit `0` after doing no
work, or agents that exit `0` after asking a question into a headless run.

If Jev cannot be reached mid-run, the run is reported as unjudged and escalated
with a path to its transcript. Helm does not guess at an outcome it could not
observe, and it will not auto-retry work nobody has assessed.

## The Skill

Everything above is packaged as one skill, `helm`. `SKILL.md` is the part a
coding agent actually reads: when to reach for the harness, how to present a
verdict, and what never to do — chiefly, never read a worker transcript to
decide whether it finished. The scripts and references under it are the runnable
pieces the skill drives.

```text
.
|-- .claude-plugin/
|   |-- plugin.json          <- plugin manifest
|   `-- marketplace.json     <- lets this repo serve itself as a marketplace
`-- skills/helm/
    |-- SKILL.md             <- when to use Helm, how to read a verdict
    |-- scripts/
    |   |-- probe.py         <- inventory: agents, auth state, hardware
    |   |-- route.py         <- the routing decision
    |   |-- supervise.py     <- dispatch, then judge completion
    |   |-- dispatch.py      <- turns a Jev metric into a CLI flag
    |   |-- jev.py           <- TypeSafe Jev client
    |   |-- keystore.py      <- local API key storage
    |   `-- cards/           <- one capability card per agent CLI
    |-- references/
    |   |-- walkthrough.md
    |   |-- capability-cards.md
    |   |-- jev-decision-layer.md
    |   |-- dispatch-modes.md
    |   `-- calibration.md
    `-- tests/
```

You never invoke the skill by name. Its description is written so that questions
like *"which agent should take this refactor?"*, *"what agents do I even have
installed?"*, or *"route this build to whatever is best"* load it on their own —
as does the start of any substantial build, where picking the wrong tool is
expensive.

## Install The Plugin

Helm is a Claude Code plugin, and this repository doubles as its own
marketplace, so there is nothing to clone or copy by hand. In Claude Code:

```bash
/plugin marketplace add Jimuelle07/Helm
/plugin install helm@helm
```

The same two steps from a terminal, if you would rather not be inside a session:

```bash
claude plugin marketplace add Jimuelle07/Helm
claude plugin install helm@helm
```

Then give Helm its decision layer. This is once per user — the plugin ships no
key, because it is meant to be shared across a team and each person brings their
own TypeSafe account. The easiest way is to let the agent do it, since it
already knows where its own plugin lives:

> *"set up Helm's TypeSafe key: `apikey_...`"*

That runs `route.py --set-api-key`, which stores the key at
`~/.cache/helm/credentials.json` — outside the plugin, so it survives every
update — and prints only a masked form of it back. Two alternatives if you would
rather not hand a key to an agent: export `TYPESAFE_API_KEY` in your shell, which
always takes precedence, or run the script yourself from the installed copy under
`~/.claude/plugins/cache/helm/helm/<version>/`.

Confirm the whole path works with one live inference call:

> *"check whether Helm can reach Jev"*   →   `probe.py --check-jev`, exit `0`

Then confirm the skill loads on its own by asking something it is written to
catch — *"what coding agents are installed on this machine?"* — and watch it
reach for `probe.py` without being told to.

Requirements are Python 3.10+ on `PATH` and a TypeSafe API key. Nothing is
installed into your Python environment; the scripts are standard library only.

Housekeeping later on:

```bash
claude plugin update helm@helm             # pull a newer version
claude plugin marketplace update helm      # refresh the marketplace listing
claude plugin uninstall helm@helm          # remove it
```

### Without the plugin

Any other agent — or a plain shell — can use Helm by cloning it and calling the
scripts directly, which is exactly what [Quick Start](#quick-start) shows:

```bash
git clone https://github.com/Jimuelle07/Helm.git
cd Helm
python skills/helm/scripts/probe.py --repo .
```

For agents that read skills from a directory, point them at `skills/helm/` or
copy that folder into wherever they keep theirs — it is self-contained.

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
| `skills/helm/SKILL.md` | Skill workflow and result-presentation rules |
| `skills/helm/references/capability-cards.md` | Card schema and guidance for writing routing-friendly competence lines |
| `skills/helm/references/jev-decision-layer.md` | The decision layer in depth: both question sets, the typed primitives, the API contract, and why there is no fallback |
| `skills/helm/references/dispatch-modes.md` | How a Jev metric becomes a CLI flag: the five modes, the four rules, the recovery table |
| `skills/helm/references/calibration.md` | Thresholds, assumptions, and how to fit them against recorded traces |

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
- Jev is a hard dependency. Helm orchestrates by *judging* — which agent, how
  risky, is it finished — so with the decision layer unreachable there is
  nothing left to orchestrate with but an inventory, which is what `probe.py`
  prints before it exits non-zero.
