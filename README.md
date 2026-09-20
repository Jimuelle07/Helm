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

## The Problem

You have `claude`, `codex`, `cursor-agent`, `gemini`, `aider`, `opencode`,
`copilot`, maybe a local model. Three things go wrong with that, every day.

**Agents cannot see each other.** An LLM knows only the tools listed in its
prompt, so the sibling CLIs sitting in the same `PATH` — each with a different
model, price, context window and speciality — are invisible to it. Whichever
agent you happened to open is the agent that does the work, right tool or not.
When one strays outside its competence it finds out by *failing*: attempt, burn
tokens, retry, apologise. That knowledge is paid for, then thrown away when the
session ends.

**Installed is not logged in.** Installing an agent and authenticating it are
separate acts, and people routinely do the first without the second. The CLI
passes a `--version` check and looks healthy right up to the moment it tries to
call a model.

**`exit 0` is not "done".** Agents exit `0` after describing work they never
did, and exit `0` after asking a clarifying question into a headless run where
nobody can answer. The only reliable way to tell is to read the transcript —
tens of thousands of tokens of build noise that costs real money to read and
then permanently occupies the orchestrator's context for the rest of the
session.

The common thread is **judgement**: which agent, how risky, is it finished.
Judgement is the expensive part of orchestration, and you cannot afford to spend
a frontier model on it for every task.

## The Solution

Split the work three ways, and give each part to whatever is cheapest and safest
at it:

> **The probe observes. Jev judges. The chosen agent generates.**

| Script | Job | Role |
| --- | --- | --- |
| `skills/helm/scripts/probe.py` | Finds installed agents, auth state, hardware, and fixes | Observes |
| `skills/helm/scripts/route.py` | Chooses the best available agent for a task | Asks Jev |
| `skills/helm/scripts/supervise.py` | Dispatches a worker and judges whether it finished | Asks Jev |

The probe reads the machine and calls no models.
[TypeSafe Jev](https://typesafe.ai) — a non-generative **System One** model that
returns typed probabilistic decisions instead of text — makes every judgement
call, for about **$0.0001**. Only then does a real coding agent write code.

The keystone is that the answer space handed to Jev is built from what the probe
found, so recommending an agent you do not have is *structurally impossible*
rather than merely unlikely. That is the whole reason to use a typed decision
model here instead of asking an LLM to please only suggest installed tools.

### One decision, end to end

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

One request, about a second, roughly $0.0001 — and the prompt handed to
`cursor-agent` was assembled from those metrics rather than passed through
unchanged.

## Install

Helm is one skill in the agent-neutral `SKILL.md` format, so it installs
natively wherever you already work. Pick your host; the decision-layer step at
the end is the same for all of them.

### Claude Code

This repository doubles as its own plugin marketplace, so there is nothing to
clone:

```bash
/plugin marketplace add Jimuelle07/Helm
/plugin install helm@helm
```

From a terminal instead: `claude plugin marketplace add Jimuelle07/Helm`, then
`claude plugin install helm@helm`. Later: `claude plugin update helm@helm`,
`claude plugin marketplace update helm`, `claude plugin uninstall helm@helm`.

### Gemini CLI

The repository is also a Gemini CLI extension; the skill under `skills/` comes
with it.

```bash
gemini extensions install https://github.com/Jimuelle07/Helm
```

Then `gemini extensions list` / `update helm` / `uninstall helm`.

### Codex, Cursor, OpenCode, and 80+ others

[`npx skills`](https://github.com/vercel-labs/skills) is a package manager for
Agent Skills that knows where each agent keeps its own. It reads this repository
directly — no clone:

```bash
npx skills add Jimuelle07/Helm            # choose agents interactively
npx skills add Jimuelle07/Helm -g -a '*'  # globally, into every agent it finds
npx skills add Jimuelle07/Helm --list     # just show what the repo contains
```

`-a` takes agent ids (`claude-code`, `opencode`, …); `-g` installs for your user
rather than the current project. Afterwards: `npx skills list`,
`npx skills update helm`, `npx skills remove helm`.

### From a clone, into any skills directory

These agents all read Agent Skills off disk, so installing is just putting
`skills/helm/` where the agent looks. `install.sh` does that by symlink, so one
`git pull` updates every agent at once:

```bash
git clone https://github.com/Jimuelle07/Helm.git
cd Helm
./install.sh                  # ~/.agents/skills -- the shared location
./install.sh codex cursor     # or each agent's own directory as well
```

On Windows use `.\install.ps1`, which takes the same arguments and links with a
junction — no administrator rights, no developer mode.

| Argument | Installs to | Read by |
| --- | --- | --- |
| *(none)* | `~/.agents/skills/helm` | Codex, Cursor, OpenCode, and other Agent Skills hosts |
| `codex` | `$CODEX_HOME/skills/helm` | Codex CLI |
| `cursor` | `~/.cursor/skills/helm` | Cursor |
| `opencode` | `~/.config/opencode/skills/helm` | OpenCode |
| `claude` | `~/.claude/skills/helm` | Claude Code, without going through the plugin |
| `project` | `./.agents/skills/helm` | Any of the above, scoped to one repository |

`--copy` copies instead of linking, `--list` shows where Helm is installed,
`--uninstall` removes it. For an agent on none of these paths, copy
`skills/helm/` into whatever directory it scans — the folder is self-contained —
or skip the skill and call the scripts directly. Nothing in Helm depends on
having been installed.

### Then, on every host

Requirements are Python 3.10+ on `PATH` and a TypeSafe API key. Nothing is
installed into your Python environment; the scripts are standard library only.
One more step before anything routes — [set up Jev](#1-set-up-jev-once-per-user).

## Using It

### 1. Set up Jev, once per user

Jev is the decision layer, not an accelerator, so this is a prerequisite rather
than a nicety. The skill deliberately ships with no key: it is meant to be
shared across a team, and each person brings their own TypeSafe account, the
same way they would with `gh auth login`.

Get a key from your TypeSafe account, then:

```bash
python skills/helm/scripts/route.py --set-api-key apikey_...   # probe.py takes it too
python skills/helm/scripts/probe.py --check-jev                # exit 0 = Helm can route
```

Installed as a skill, you can skip the paths entirely and just say *"set up
Helm's TypeSafe key: `apikey_...`"* — the agent knows where its own copy lives.

The key lands in `~/.cache/helm/credentials.json` (owner-only on POSIX), which
sits outside the skill, so it survives updates and is shared by every agent you
installed Helm into. It is only ever echoed back masked (`apike...bf32`).
`--clear-api-key` removes it, and `TYPESAFE_API_KEY` in the environment always
takes precedence — useful for CI or a temporary override.

Run `--check-jev` rather than trusting the key on sight: it makes one real
inference call, so it catches a key that lists models fine but fails at routing
time.

### 2. The normal way: just ask

Once Helm is installed you never name it. Its description is written so the
questions you would already ask pull it in:

| You say | Helm runs | You get |
| --- | --- | --- |
| *"what coding agents do I have?"* | `probe.py` | Inventory, auth state, fixes |
| *"which agent should do this refactor?"* | `route.py` | A choice, a command, the gates |
| *"have codex fix the failing parser test"* | `supervise.py` | A verdict, not a transcript |

Default behaviour is to recommend, not to run. The agent should show you the
command and the reason it picked that tool; it only dispatches when you ask it
to, and the gates can still refuse.

### 3. Or drive the three scripts yourself

```bash
# what is on this machine
python skills/helm/scripts/probe.py --repo .

# who should do this task
python skills/helm/scripts/route.py "add retry logic to the payment client" --repo .

# hand it over, and let Jev judge the result
python skills/helm/scripts/supervise.py codex "fix the failing test in src/parser.py" --watch
```

| Script | Worth knowing |
| --- | --- |
| `probe.py` | `--refresh` bypasses the 24h cache · `--json` raw registry · `--no-verify-auth` skips the login checks (faster) · `--check-jev` |
| `route.py` | `--json` full verdict · `--execute` runs it if every gate passes · `--timeout` (default 900) · `--watch` · `--refresh` |
| `supervise.py` | `--watch` stops a run that is looping or waiting · `--timeout` · `--retry N` (default 1, `0` disables) · `--signals-file` · `--no-modes` |

### 4. Chaining a decision into a dispatch

The task-tuned flags come from Jev's metrics, so hand the verdict to the
supervisor rather than re-typing the command:

```bash
python skills/helm/scripts/route.py "..." --json > decision.json
python skills/helm/scripts/supervise.py claude "..." --signals-file decision.json
```

`route.py --execute` does exactly this in one step. Without signals the
supervisor dispatches the card's plain base contract — deliberately, because
nothing here should widen an agent's permissions on the strength of a metric
nobody supplied. `--no-modes` forces that base contract even when signals exist.

### Exit codes

| | |
| --- | --- |
| `probe.py --check-jev` | `0` can route · `1` cannot |
| `route.py` | `0` ok · `1` no routable agents · `2` no API key · `3` gates blocked `--execute` · `4` Jev unreachable |
| `supervise.py` | `0` done/uncertain · `1` needs attention · `2` no API key |

## How It Works

### 1. Probe — observe, without calling a model

`probe.py` inventories the machine: hardware, every installed agent with its
version, and a one-line *competence* from its [capability
card](skills/helm/references/capability-cards.md). Results cache for 24 hours
(`--refresh` to force a re-probe, `--json` for the raw registry).

It also asks each CLI whether it is logged in, and the rule is deliberately
asymmetric. *"We could not find a credential"* never blocks, because tokens live
in keychains no probe can enumerate. Only *"the tool's own status command says
it is logged out"* removes an agent from routing:

```text
INSTALLED BUT NOT AUTHENTICATED (1)  <- log in to use these
  codex          Not logged in.
                 fix: codex login
```

### 2. Jev — judge, in one typed request

`route.py` hands Jev the task plus the registry and asks **seven questions,
evaluated independently and in parallel in a single request**:

| Question | Type | What it decides |
| --- | --- | --- |
| `agent` | Choice (**dynamic**) | Which installed agent takes this task |
| `task_kind` | Choice (10) | scaffold / feature / bugfix / refactor / test / docs / … |
| `blast_radius` | Score (4) | How much damage a wrong edit does |
| `spec_clarity` | Score (4) | Is this specified enough to hand off at all? |
| `context_breadth` | Score (4) | How much of the codebase must be understood |
| `needs_human` | Noul | Should a person approve this before it runs? |
| `reversible` | Noul | Is `git checkout` a sufficient undo? |

`agent` is a `Choice` whose options are constructed at call time:

```python
criteria = {a["name"]: a["competence"] for a in routable}
criteria["none"] = "No installed agent is a good fit; escalate to the user"
```

Jev cannot return a value outside that space, so *"only recommend what is
installed"* becomes a property of the schema rather than an instruction you hope
a model respects. And because it returns a distribution rather than a pick, low
confidence is real information — usually a genuine near-tie, which trips a gate
and downgrades the run to a recommendation.

Measured against `jev-1.13.0`: **0.7 s, 2,149 input tokens, $0.00009**. Input is
$0.042 per million tokens, output is billed at zero. That price is the point —
it is what lets the control plane judge *every* task instead of only the ones
that look hard.

The verdict's `mode` is the decision:

| Mode | Meaning |
| --- | --- |
| `auto` | Every gate passed; safe to run |
| `recommend` | Good agent found, but a gate is unmet — show the command, let the user decide |
| `clarify` | Too vague to hand off; ask the one question that resolves it |
| `escalate` | No installed agent fits |

### 3. Dispatch — the command is built from the metrics

Jev's numbers are translated into that agent's own flags and preamble before the
command is printed. A high `blast_radius` runs the change in a git worktree; a
wide `context_breadth` injects `parallel`; a `review` task gets
`gemini --approval-mode plan` and literally cannot write. Five modes, four
rules, in [`dispatch-modes.md`](skills/helm/references/dispatch-modes.md).

Two notes Helm prints, and you should pass on: `modes this agent cannot express`
means the chosen CLI has no way to do what the metrics asked for, and
`caveat: injected flags for X are unverified` means that flag came from a cheat
sheet rather than the CLI's own `--help`.

### 4. Supervise — judge the result, not the exit code

`supervise.py` dispatches the worker, then sends the transcript to Jev instead
of to your context. The transcript goes to disk; you get a few hundred bytes:

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

`no_op` and `stuck` are the two an exit code cannot see. Both are recoverable:
the supervisor retries once by default with that agent's own cure — Aider's
`/undo`, a "you are headless, nobody can answer you" preamble, an
apply-the-edits-now instruction. `--watch` polls a run in flight and stops it
early if Jev sees it looping or waiting for input. A `timeout` never auto-retries,
because the tree may be half-modified.

### When Jev cannot be reached, Helm stops

There is no second scorer. No key → `route.py` and `supervise.py` print setup
instructions and exit `2`. Key present but the API unreachable → `route.py`
exits `4` with the API error and no route. Jev dies *after* the worker ran → the
run is reported unjudged, with its transcript path, and is never auto-retried.

This is deliberate: the questions Helm asks are exactly the ones local
heuristics answer badly. Telling "wrote the code" from "wrote *about* the code"
needs a reader, not a keyword scan, and a fallback that cannot tell would be
confidently wrong on precisely the cases the tool exists to catch. Every
decision is logged to `~/.cache/helm/decisions.jsonl` so the thresholds can be
fitted against real traces later.

### Layout

```text
.
|-- .claude-plugin/          <- plugin + marketplace manifests (Claude Code)
|-- gemini-extension.json    <- Gemini CLI extension manifest
|-- install.sh / install.ps1 <- drop the skill into any other agent
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
    `-- tests/
```

You never invoke the skill by name — its description is written so that *"which
agent should take this refactor?"* or *"what agents do I even have?"* loads it
on its own.

## Teaching Helm A New Agent

Capability cards in `skills/helm/scripts/cards/*.json` are the declared
knowledge that lets Helm reason about an agent without invoking it. One file
adds a CLI:

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

`competence` is what Jev reads, so treat it as a routing parameter, not
documentation: write it to distinguish this tool from the other cards.

## Documentation

| File | Purpose |
| --- | --- |
| [`walkthrough.md`](skills/helm/references/walkthrough.md) | Start here: install to end-to-end, with real output |
| [`SKILL.md`](skills/helm/SKILL.md) | Skill workflow and result-presentation rules |
| [`capability-cards.md`](skills/helm/references/capability-cards.md) | Card schema; writing competence lines that discriminate |
| [`jev-decision-layer.md`](skills/helm/references/jev-decision-layer.md) | Both question sets, the typed primitives, the API contract |
| [`dispatch-modes.md`](skills/helm/references/dispatch-modes.md) | How a metric becomes a CLI flag: five modes, four rules |
| [`calibration.md`](skills/helm/references/calibration.md) | Thresholds, and how to fit them against recorded traces |

## Honest Limitations

- **Every threshold is uncalibrated.** They are conservative starting points;
  decisions log to `~/.cache/helm/decisions.jsonl` so they can be fitted later.
- **The Jev wire contract is verified as of 2026-09-20 with `jev-1.13.0`.**
  `GET /v1/models` lists only the `jev-latest` and `jev-preview` aliases, so the
  concrete version cannot be discovered there; traces record the resolved
  version so drift stays detectable.
- **Some capability cards are best effort.** Cards whose invocation or auth
  commands have not been verified are flagged at runtime.
- **Jev is a hard dependency.** With the decision layer unreachable there is
  nothing left to orchestrate with but an inventory, which is what `probe.py`
  prints before it exits non-zero.

## FAQ

**Is this Helm, the Kubernetes package manager?** No, and there is no relation —
never use it for charts, `helm install`, releases or clusters. Name collision,
nothing more.

**Which coding agents can it route to?** Claude Code, Codex, Cursor Agent,
Gemini CLI, Aider, OpenCode, GitHub Copilot, Amp, Droid, Crush, Goose, Qwen Code
and Ollama-hosted local models — one capability card each.

**Which agents can run Helm itself?** Any that reads Agent Skills: Claude Code,
Codex, Cursor, OpenCode, Gemini CLI and 80+ more. See [Install](#install).

**Do I need a TypeSafe account?** Yes — Jev is the decision layer, not an
optional accelerator, and there is no fallback scorer. Routing costs about
$0.0001 per task.

**Does it work on Windows?** Yes — Windows, macOS and Linux, Python 3.10+, no
third-party packages.

## Credits

Helm was built by **[Jimuelle Patron](https://github.com/Jimuelle07)** and is
released under the [MIT license](LICENSE). The decision layer is
[TypeSafe Jev](https://typesafe.ai).

If Helm routed something well — or badly — that disagreement is the useful
signal: [open an issue](https://github.com/Jimuelle07/Helm/issues).
