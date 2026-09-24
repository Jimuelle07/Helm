<h1 align="center">Helm</h1>

<p align="center">
  <strong>Steering and control for the coding agents already on your machine.</strong>
</p>

<p align="center">
  An AI coding-agent orchestrator and model router for Claude Code, Codex,
  Cursor, Gemini CLI, Aider, OpenCode, Copilot and local models.
  Installable as a Claude Code plugin, a Gemini CLI extension, or an Agent Skill.
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

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#why-helm">Why Helm</a> ·
  <a href="#install">Install</a> ·
  <a href="#using-helm">Usage</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#faq">FAQ</a>
</p>

> [!NOTE]
> Not related to Helm, the Kubernetes package manager. This Helm steers AI
> coding agents.

<br>

## Quick start

**1. Install** (Claude Code shown here; [other hosts below](#install)):

```bash
/plugin marketplace add Jimuelle07/Helm
/plugin install helm@helm
```

**2. Add your TypeSafe key** (once per user):

```bash
python skills/helm/scripts/route.py --set-api-key apikey_...
python skills/helm/scripts/probe.py --check-jev     # exit 0 = ready
```

**3. Just ask your agent:**

> *"Which agent should do this refactor?"*

Helm answers with the best installed agent, the exact command to run, and
the reason it chose that agent.

<br>

## Why Helm

You probably have several coding agents installed: `claude`, `codex`,
`cursor-agent`, `gemini`, `aider`, `opencode`, `copilot`, maybe a local model.
Three things go wrong every day:

- **Agents can't see each other.**
  An agent only knows the tools in its own prompt. Whichever one you opened
  does the work, right tool or not, and it discovers its limits by failing and
  burning tokens.

- **Installed isn't logged in.**
  A CLI passes `--version` and looks healthy right up until it tries to call a
  model.

- **`exit 0` isn't "done".**
  Agents exit `0` after *describing* work they never did, or after asking a
  question nobody can answer in a headless run. Finding out means reading
  thousands of tokens of transcript.

All three are **judgement** calls: which agent, how risky, is it finished.
Judgement is the expensive part of orchestration, and a frontier model is too
costly to spend on it for every task.

<br>

## The idea

> **The probe observes. Jev judges. The chosen agent generates.**

```text
  your task
      │
      ▼
  ┌─────────┐   what's installed,     ┌─────────┐   which agent, how    ┌──────────────┐
  │  probe  │ ─ logged in, hardware ─▶│   Jev   │ ─ risky, is it done ─▶│ coding agent │
  └─────────┘   (no model calls)      └─────────┘   (~$0.0001 each)     └──────────────┘
```

| Script | Job | Role |
| --- | --- | --- |
| `probe.py` | Finds installed agents, auth state, hardware, and fixes | Observes |
| `route.py` | Chooses the best available agent for a task | Asks Jev |
| `supervise.py` | Dispatches a worker and judges whether it finished | Asks Jev |

[TypeSafe Jev](https://typesafe.ai) is a non-generative **System One** model.
It returns typed probabilistic decisions instead of text, for about
**$0.0001** per decision.

The key design choice: Jev's list of answers is built from what the probe
found. It is *structurally impossible* for Helm to recommend an agent you don't
have.

### Example: one routing decision

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

One request, about a second, roughly $0.0001. The prompt sent to
`cursor-agent` was built from these signals, not passed through unchanged.

<details>
<summary><b>What each signal did</b></summary>

<br>

| Signal | Value | What it caused |
| --- | --- | --- |
| `agent` | cursor-agent @ 0.97 | Picked the semantic indexer over Claude (0.02), not a close call |
| `context_breadth` | 2.68 | Repo-wide, so `parallel` mode was injected into the command |
| `task_kind` | refactor | Selected the find-all-call-sites-first preamble |
| `needs_human` | 0.62 | Crossed the 0.5 gate, so `RECOMMEND` rather than auto-execute |
| `blast_radius` | 1.98 | Under the 2.0 ceiling, so this gate stayed quiet |

</details>

<br>

## Install

Helm is a single skill in the agent-neutral `SKILL.md` format, so it installs
natively wherever you already work.

**Requirements:** Python 3.10+ on `PATH` and a TypeSafe API key. Nothing gets
installed into your Python environment, because the scripts use only the
standard library.

Choose your host below. Setup afterwards is the same for all of them
([step 1 of Usage](#1-set-up-jev-once-per-user)).

<details>
<summary><b>Claude Code</b></summary>

<br>

This repository is also its own plugin marketplace, so you don't need to
clone it:

```bash
/plugin marketplace add Jimuelle07/Helm
/plugin install helm@helm
```

From a terminal:

```bash
claude plugin marketplace add Jimuelle07/Helm
claude plugin install helm@helm
```

Update or remove with `claude plugin update helm@helm`,
`claude plugin marketplace update helm`, or `claude plugin uninstall helm@helm`.

</details>

<details>
<summary><b>Gemini CLI</b></summary>

<br>

The repository is also a Gemini CLI extension, and it includes the skill.

```bash
gemini extensions install https://github.com/Jimuelle07/Helm
```

Manage it with `gemini extensions list`, `update helm`, or `uninstall helm`.

</details>

<details>
<summary><b>Codex, Cursor, OpenCode, and 80+ others</b></summary>

<br>

[`npx skills`](https://github.com/vercel-labs/skills) is a package manager for
Agent Skills. It knows where each agent keeps its skills and reads this
repository directly:

```bash
npx skills add Jimuelle07/Helm            # choose agents interactively
npx skills add Jimuelle07/Helm -g -a '*'  # globally, into every agent it finds
npx skills add Jimuelle07/Helm --list     # just show what the repo contains
```

- `-a` takes agent ids (`claude-code`, `opencode`, …).
- `-g` installs for your user instead of the current project.

Afterwards: `npx skills list`, `npx skills update helm`, `npx skills remove helm`.

</details>

<details>
<summary><b>From a clone, into any skills directory</b></summary>

<br>

Installing just means putting `skills/helm/` where the agent looks.
`install.sh` does that with a symlink, so one `git pull` updates every agent:

```bash
git clone https://github.com/Jimuelle07/Helm.git
cd Helm
./install.sh                  # ~/.agents/skills, the shared location
./install.sh codex cursor     # or each agent's own directory as well
```

On Windows, use `.\install.ps1`. It takes the same arguments and links with a
junction, so it doesn't need administrator rights or developer mode.

| Argument | Installs to | Read by |
| --- | --- | --- |
| *(none)* | `~/.agents/skills/helm` | Codex, Cursor, OpenCode, and other Agent Skills hosts |
| `codex` | `$CODEX_HOME/skills/helm` | Codex CLI |
| `cursor` | `~/.cursor/skills/helm` | Cursor |
| `opencode` | `~/.config/opencode/skills/helm` | OpenCode |
| `claude` | `~/.claude/skills/helm` | Claude Code, without going through the plugin |
| `project` | `./.agents/skills/helm` | Any of the above, scoped to one repository |

Other flags: `--copy` copies instead of linking, `--list` shows where Helm is
installed, and `--uninstall` removes it.

For an agent that uses a different path, copy `skills/helm/` into whatever
directory it scans. The folder is self-contained.

</details>

<br>

## Using Helm

### 1. Set up Jev, once per user

Jev is the decision layer, so Helm can't route without it.

Helm ships without a key because it's meant to be shared across a team. Each
person uses their own TypeSafe account, the same way everyone runs their own
`gh auth login`.

```bash
python skills/helm/scripts/route.py --set-api-key apikey_...   # probe.py accepts it too
python skills/helm/scripts/probe.py --check-jev                # exit 0 = Helm can route
```

> [!TIP]
> If you installed Helm as a skill, you can skip the paths and just say
> *"set up Helm's TypeSafe key: `apikey_...`"*.

- The key is saved to `~/.cache/helm/credentials.json` (owner-only on POSIX).
  That file is outside the skill, so the key survives updates and every agent
  you installed Helm into can use it.
- It's only ever shown masked, as `apike...bf32`.
- `--clear-api-key` removes it. `TYPESAFE_API_KEY` in the environment always
  wins, which is handy for CI.
- `--check-jev` makes one real inference call, so it also catches a key that
  can list models but fails when it's time to route.

### 2. Just ask

Once Helm is installed you don't need to name it. It runs when you ask
questions like these:

| You say | Helm runs | You get |
| --- | --- | --- |
| *"what coding agents do I have?"* | `probe.py` | Inventory, auth state, fixes |
| *"which agent should do this refactor?"* | `route.py` | A choice, a command, the gates |
| *"have codex fix the failing parser test"* | `supervise.py` | A verdict, not a transcript |

By default Helm **recommends and doesn't run anything**. It shows you the
command and why it chose that agent. It only dispatches when you ask, and the
gates can still refuse.

### 3. Or run the scripts yourself

```bash
# what is on this machine
python skills/helm/scripts/probe.py --repo .

# who should do this task
python skills/helm/scripts/route.py "add retry logic to the payment client" --repo .

# hand it over, and let Jev judge the result
python skills/helm/scripts/supervise.py codex "fix the failing test in src/parser.py" --watch
```

To reuse a routing decision, pass the verdict to the supervisor instead of
retyping the command (`route.py --execute` does both in one step):

```bash
python skills/helm/scripts/route.py "..." --json > decision.json
python skills/helm/scripts/supervise.py claude "..." --signals-file decision.json
```

Without signals, the supervisor dispatches the agent's plain base command.
That's deliberate: Helm won't widen an agent's permissions based on a metric
nobody supplied. `--no-modes` forces the base command even when signals exist.

<details>
<summary><b>Flags and exit codes</b></summary>

<br>

**`probe.py`**
- `--refresh`: bypass the 24h cache
- `--json`: raw registry
- `--no-verify-auth`: skip login checks
- `--check-jev`: verify the key with a real call

**`route.py`**
- `--json`: full verdict
- `--execute`: run it if every gate passes
- `--timeout`: seconds (default 900)
- `--watch`, `--refresh`

**`supervise.py`**
- `--watch`: stop a run that is looping or waiting
- `--timeout`, `--retry N` (default 1)
- `--signals-file`, `--no-modes`

| Command | Exit codes |
| --- | --- |
| `probe.py --check-jev` | `0` can route · `1` cannot |
| `route.py` | `0` ok · `1` no routable agents · `2` no API key · `3` gates blocked `--execute` · `4` Jev unreachable |
| `supervise.py` | `0` done or uncertain · `1` needs attention · `2` no API key |

</details>

<br>

## How it works

Helm runs every task through four stages: **probe → judge → dispatch →
supervise**.

### 1. Probe: observe without calling a model

`probe.py` inventories the machine: hardware, every installed agent and its
version, and a one-line *competence* from the agent's
[capability card](skills/helm/references/capability-cards.md). Results are
cached for 24 hours.

It also asks each CLI whether it's logged in, and that check is deliberately
lopsided:

- *"We couldn't find a credential"* **never** blocks an agent, because tokens
  can be stored in keychains the probe can't read.
- *"The tool's own status command says it's logged out"* **does** remove the
  agent from routing.

```text
INSTALLED BUT NOT AUTHENTICATED (1)  <- log in to use these
  codex          Not logged in.
                 fix: codex login
```

### 2. Judge: seven questions in one typed request

`route.py` sends Jev the task and the registry, and asks seven questions.
Jev evaluates them independently and in parallel, all in one request:

| Question | Type | What it decides |
| --- | --- | --- |
| `agent` | Choice (**dynamic**) | Which installed agent takes this task |
| `task_kind` | Choice (10) | scaffold / feature / bugfix / refactor / test / docs / ... |
| `blast_radius` | Score (4) | How much damage a wrong edit does |
| `spec_clarity` | Score (4) | Is this specified enough to hand off at all? |
| `context_breadth` | Score (4) | How much of the codebase must be understood |
| `needs_human` | Noul | Should a person approve this before it runs? |
| `reversible` | Noul | Is `git checkout` a sufficient undo? |

The `agent` options are built when the request is made, from the agents that
are actually usable:

```python
criteria = {a["name"]: a["competence"] for a in routable}
criteria["none"] = "No installed agent is a good fit; escalate to the user"
```

Jev can't return anything outside that list, so *"only recommend installed
agents"* is guaranteed by the schema. You don't have to rely on a model
following instructions.

Jev returns a probability for each option, not just a single pick, so low
confidence tells you something. It usually means a real near-tie, which trips
a gate and downgrades the run to a recommendation.

**Cost:** measured against `jev-1.13.0` at 0.7 s, 2,149 input tokens,
**$0.00009**. Input costs $0.042 per million tokens and output is free. At
that price, Helm can judge *every* task, not just the ones that look hard.

The result is one of four modes:

| Mode | Meaning |
| --- | --- |
| `auto` | Every gate passed, so it's safe to run |
| `recommend` | Found a good agent, but a gate isn't met. Shows the command and lets you decide |
| `clarify` | The task is too vague to hand off. Asks the one question that would resolve it |
| `escalate` | No installed agent fits |

### 3. Dispatch: build the command from the signals

Before the command is printed, Jev's numbers are turned into that agent's own
flags and prompt preamble. For example:

- High `blast_radius` → the change runs in a **git worktree**.
- Wide `context_breadth` → **`parallel`** mode is injected.
- A `review` task → `gemini --approval-mode plan`, which **cannot write** files.

[`dispatch-modes.md`](skills/helm/references/dispatch-modes.md) covers all five
modes and four rules.

### 4. Supervise: judge the result, not the exit code

`supervise.py` runs the agent and sends the transcript to Jev instead of into
your context. The transcript is saved to disk, and you get a short summary:

```text
DONE -- codex (completed)
  Edited src/parser.py and ran the suite: 14 passed
  exit 0 after 47.2s | judged by jev
  next: accept
  full transcript: /tmp/helm-runs/codex-1758.log
```

| Outcome | Meaning | `next` |
| --- | --- | --- |
| `done` | Carried out, high confidence | `accept` |
| `uncertain` / `incomplete` | Looks finished, or claimed finished but was not | `review` |
| `no_op` | Described the work instead of doing it | `retry` |
| `stuck` | Waiting on input that cannot arrive headlessly | `ask_user` |
| `failed` | Errored out | `retry` |
| `timeout` / `error` | Deadline hit, or a precondition failed | `escalate` |

An exit code can't detect **`no_op`** or **`stuck`**. Both can be recovered
from: by default the supervisor retries once using that agent's own fix, like
Aider's `/undo` or an "apply the edits now" instruction.

- `--watch` checks a run while it's in progress and stops it early if Jev sees
  it looping or waiting for input.
- A `timeout` is never retried automatically, because the files may be only
  partly changed.

### If Jev can't be reached, Helm stops

There is no backup scorer.

| Situation | What happens |
| --- | --- |
| No key | `route.py` and `supervise.py` print setup instructions and exit `2` |
| API unreachable | `route.py` reports the API error and exits `4`, with no route |
| Jev fails after the agent ran | Reported unjudged, with the transcript path, and never auto-retried |

This is on purpose. Helm's questions are the kind simple local rules answer
badly: telling "wrote the code" apart from "wrote *about* the code" takes
actually reading the transcript, not searching for keywords. A fallback like
that would be confidently wrong on exactly the cases Helm is meant to catch.

Every decision is logged to `~/.cache/helm/decisions.jsonl`, so the thresholds
can be tuned against real runs later.

<br>

## Documentation

| File | Purpose |
| --- | --- |
| [`walkthrough.md`](skills/helm/references/walkthrough.md) | **Start here:** from install to a full run, with real output |
| [`SKILL.md`](skills/helm/SKILL.md) | Skill workflow and rules for presenting results |
| [`capability-cards.md`](skills/helm/references/capability-cards.md) | Card schema, and how to write competence lines that tell agents apart |
| [`jev-decision-layer.md`](skills/helm/references/jev-decision-layer.md) | Both question sets, the typed primitives, the API contract |
| [`dispatch-modes.md`](skills/helm/references/dispatch-modes.md) | How a signal becomes a CLI flag: five modes, four rules |
| [`calibration.md`](skills/helm/references/calibration.md) | Thresholds, and how to tune them against recorded runs |

**Adding a new agent** takes one JSON file in `skills/helm/scripts/cards/`. It
holds the binary names, how to run it headlessly, how to check its login, and
a `competence` line that contrasts it with other agents. Jev reads that line
to make routing decisions, so write it carefully.

<br>

## Limitations

- **Thresholds aren't calibrated yet.** They're cautious starting points.
  Decisions are logged to `~/.cache/helm/decisions.jsonl` so they can be tuned.
- **The Jev API contract was verified on 2026-09-20 against `jev-1.13.0`.**
  `GET /v1/models` only lists the `jev-latest` and `jev-preview` aliases, so
  it doesn't show the exact version. Each logged run records the version that
  answered, so changes can still be detected.
- **Some capability cards are best effort.** Cards with unverified run or auth
  commands are flagged when Helm runs.
- **Jev is a hard dependency.** Without it, Helm can only list your agents.

<br>

## FAQ

<details>
<summary><b>Is this Helm, the Kubernetes package manager?</b></summary>

<br>

No, and there's no relation. Don't use it for charts, `helm install`,
releases or clusters. The shared name is a coincidence.

</details>

<details>
<summary><b>Which coding agents can it route to?</b></summary>

<br>

Claude Code, Codex, Cursor Agent, Gemini CLI, Aider, OpenCode, GitHub Copilot,
Amp, Droid, Crush, Goose, Qwen Code, and Ollama-hosted local models, with one
capability card for each.

</details>

<details>
<summary><b>Which agents can run Helm itself?</b></summary>

<br>

Any agent that reads Agent Skills: Claude Code, Codex, Cursor, OpenCode,
Gemini CLI and 80+ more. See [Install](#install).

</details>

<details>
<summary><b>Do I need a TypeSafe account?</b></summary>

<br>

Yes. Jev is the decision layer. It isn't optional, and Helm has no backup
scorer. Routing costs about $0.0001 per task.

</details>

<br>

## Credits

Helm was built by **[Jimuelle Patron](https://github.com/Jimuelle07)** and is
released under the [MIT license](LICENSE). The decision layer is
[TypeSafe Jev](https://typesafe.ai).

Did Helm route something well, or badly? Either way, that's useful feedback.
[Open an issue](https://github.com/Jimuelle07/Helm/issues).
