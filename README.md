<h1 align="center">Helm</h1>

<p align="center">
  <strong>Steering and control for the coding agents already on your machine.</strong>
</p>

<p align="center">
  An AI coding-agent orchestrator and model router for Claude Code, Codex,
  Cursor, Gemini CLI, Aider, OpenCode, Copilot and local models &mdash;
  installable as a Claude Code plugin, a Gemini CLI extension, or an Agent Skill.
</p>

<p align="center">
  Built by <a href="https://github.com/Jimuelle07">Jimuelle Patron</a>
</p>

<p align="center">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-FFD43B?style=plastic&logo=python&logoColor=1F2937&labelColor=3776AB">
  <img alt="Standard library only" src="https://img.shields.io/badge/Dependencies-stdlib%20only-34D399?style=plastic&logo=dependabot&logoColor=white&labelColor=065F46">
  <img alt="Powered by TypeSafe Jev" src="https://img.shields.io/badge/Decisions-TypeSafe%20Jev-C084FC?style=plastic&logo=sparkles&logoColor=white&labelColor=6D28D9">
  <img alt="Cross platform" src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-93C5FD?style=plastic&logo=windows&logoColor=white&labelColor=1E3A8A">
  <img alt="MIT license" src="https://img.shields.io/badge/License-MIT-5EEAD4?style=plastic&logo=opensourceinitiative&logoColor=white&labelColor=0F766E">
  <img alt="Installs as a plugin, an extension, or a skill" src="https://img.shields.io/badge/Installs%20as-plugin%20%7C%20extension%20%7C%20skill-F59E0B?style=plastic&logo=githubactions&logoColor=white&labelColor=92400E">
</p>

> Not related to Helm, the Kubernetes package manager. This Helm steers AI
> coding agents.

Helm is an orchestrator for coding agents. It discovers which agent CLIs are
installed and authenticated on your machine, routes each task to the best one,
dispatches it, and judges whether the work actually got done.

It ships as one skill in the agent-neutral `SKILL.md` format: a Claude Code
plugin, a Gemini CLI extension, and an `npx skills add` away on Codex, Cursor,
OpenCode and 80+ others. Two commands on any of them, and the agent you are
already talking to gains an inventory of every sibling agent on the box. See
[Install](#install).

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
The commands below drive the scripts directly from a clone; to install Helm
into an agent instead, see [Install](#install).

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
|   |-- plugin.json          <- Claude Code plugin manifest
|   `-- marketplace.json     <- lets this repo serve itself as a marketplace
|-- gemini-extension.json    <- Gemini CLI extension manifest
|-- install.sh               <- drop the skill into any other agent
|-- install.ps1              <- the same, for PowerShell
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

## Install

Helm is a single skill written in the agent-neutral `SKILL.md` format, so it
installs natively wherever you already work. Pick your host — the decision-layer
step at the end is the same for all of them.

### Claude Code

This repository doubles as its own plugin marketplace, so there is nothing to
clone or copy by hand:

```bash
/plugin marketplace add Jimuelle07/Helm
/plugin install helm@helm
```

The same two steps from a terminal, if you would rather not be inside a session:

```bash
claude plugin marketplace add Jimuelle07/Helm
claude plugin install helm@helm
```

Housekeeping later on:

```bash
claude plugin update helm@helm             # pull a newer version
claude plugin marketplace update helm      # refresh the marketplace listing
claude plugin uninstall helm@helm          # remove it
```

### Gemini CLI

The repository is also a Gemini CLI extension, and the skill under `skills/` is
discovered as part of installing it:

```bash
gemini extensions install https://github.com/Jimuelle07/Helm
```

Then `gemini extensions list` to confirm, `gemini extensions update helm` to
upgrade, `gemini extensions uninstall helm` to remove.

### Codex, Cursor, OpenCode, and everything else

The shortest path is [`npx skills`](https://github.com/vercel-labs/skills), a
package manager for Agent Skills that knows where 80+ agents keep theirs. It
reads this repository directly — no clone, no install step of ours:

```bash
npx skills add Jimuelle07/Helm            # choose agents interactively
npx skills add Jimuelle07/Helm -g -a '*'  # globally, into every agent it finds
npx skills add Jimuelle07/Helm --list     # just show what the repo contains
```

`-a` takes agent ids (`claude-code`, `opencode`, and so on); leave it off and it
asks. `-g` installs for your user rather than the current project. Afterwards,
`npx skills list`, `npx skills update helm`, and `npx skills remove helm`.

If you would rather not depend on `npx`, these agents all read Agent Skills
straight off disk, so installing is just putting `skills/helm/` where the agent
looks. `install.sh` does that — by symlink, so one `git pull` updates every
agent at once:

```bash
git clone https://github.com/Jimuelle07/Helm.git
cd Helm
./install.sh                  # ~/.agents/skills -- the shared location
./install.sh codex cursor     # or each agent's own directory as well
```

On Windows, `.\install.ps1` takes the same arguments and links with a junction,
which needs neither administrator rights nor developer mode.

| Argument | Installs to | Read by |
| --- | --- | --- |
| *(none)* | `~/.agents/skills/helm` | Codex, Cursor, OpenCode, and other Agent Skills hosts |
| `codex` | `$CODEX_HOME/skills/helm` | Codex CLI |
| `cursor` | `~/.cursor/skills/helm` | Cursor |
| `opencode` | `~/.config/opencode/skills/helm` | OpenCode |
| `claude` | `~/.claude/skills/helm` | Claude Code, without going through the plugin |
| `project` | `./.agents/skills/helm` | Any of the above, scoped to one repository |

`--copy` copies instead of linking, `--list` prints where Helm is currently
installed, and `--uninstall` removes it again.

For an agent that is not on that list, copy `skills/helm/` into whatever
directory it scans — the folder is self-contained — or skip the skill entirely
and call the scripts as in [Quick Start](#quick-start). Nothing in Helm depends
on having been installed.

### Then, on every host: the decision layer

Helm routes nothing without a TypeSafe key, and it deliberately ships with none:
the skill is meant to be shared across a team, so each person brings their own
account. The easiest way is to ask the agent, which already knows where its own
copy of the skill lives:

> *"set up Helm's TypeSafe key: `apikey_...`"*

That runs `route.py --set-api-key`, which stores the key at
`~/.cache/helm/credentials.json` — outside the skill, so it survives updates and
is shared by every agent you installed Helm into — and echoes back only a masked
form of it. To do it by hand instead, export `TYPESAFE_API_KEY`, which always
takes precedence, or run the script from wherever the skill landed:
`./install.sh --list`, or `~/.claude/plugins/cache/helm/helm/<version>/` for the
Claude Code plugin.

Confirm the whole path works with one live inference call:

> *"check whether Helm can reach Jev"*   →   `probe.py --check-jev`, exit `0`

Then confirm the skill loads on its own by asking something it is written to
catch — *"what coding agents are installed on this machine?"* — and watch it
reach for `probe.py` without being told to.

Requirements are Python 3.10+ on `PATH` and a TypeSafe API key. Nothing is
installed into your Python environment; the scripts are standard library only.

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

## FAQ

**Is this Helm, the Kubernetes package manager?** No. There is no relation. This
Helm steers AI coding agents; it has nothing to do with charts, `helm install`,
releases or clusters. It is a name collision, nothing more.

**Which coding agents does it support?** Claude Code, Codex, Cursor Agent,
Gemini CLI, Aider, OpenCode, GitHub Copilot, Amp, Droid, Crush, Goose, Qwen Code
and Ollama-hosted local models, via one [capability
card](skills/helm/references/capability-cards.md) each. Adding another is a
single JSON file.

**Which agents can run Helm itself?** Any that reads Agent Skills — Claude Code,
Codex, Cursor, OpenCode, Gemini CLI and 80+ more. See [Install](#install).

**Do I need a TypeSafe account?** Yes. Jev is the decision layer, not an
optional accelerator, and there is no fallback scorer — see
[Jev is required](#jev-is-required). Routing costs about $0.0001 per task.

**How is this different from asking an LLM which agent to use?** The answer
space is built from what the probe found, so recommending an agent you do not
have is structurally impossible rather than merely unlikely, and confidence is a
real number you can gate on. See [Why a typed decision model and not another
LLM](#why-a-typed-decision-model-and-not-another-llm).

**Does it work on Windows?** Yes — Windows, macOS and Linux, on Python 3.10+
with no third-party packages.

## Credits

Helm was built by **[Jimuelle Patron](https://github.com/Jimuelle07)** and is
released under the [MIT license](LICENSE). The decision layer is
[TypeSafe Jev](https://typesafe.ai).

If Helm routed something well — or badly — that disagreement is the useful
signal: open an issue at
[github.com/Jimuelle07/Helm](https://github.com/Jimuelle07/Helm/issues).
