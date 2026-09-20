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

> Not related to Helm, the Kubernetes package manager. This Helm steers AI
> coding agents.

## The Problem

You have `claude`, `codex`, `cursor-agent`, `gemini`, `aider`, `opencode`,
`copilot`, maybe a local model.

Three things go wrong with that, every day.

**Agents cannot see each other.**

An LLM knows only the tools listed in its prompt. The sibling CLIs sitting in
the same `PATH` are invisible to it, each with a different model, price, context
window and speciality.

So whichever agent you happened to open is the agent that does the work, right
tool or not. When one strays outside its competence, it finds out by *failing*:
attempt, burn tokens, retry, apologise. You pay for that knowledge, then lose it
when the session ends.

**Installed is not logged in.**

Installing an agent and authenticating it are separate acts, and people
routinely do the first without the second. The CLI passes a `--version` check
and looks healthy, right up to the moment it tries to call a model.

**`exit 0` is not "done".**

Agents exit `0` after describing work they never did. They also exit `0` after
asking a clarifying question into a headless run where nobody can answer.

The only reliable way to tell the difference is to read the transcript: tens of
thousands of tokens of build noise, which costs real money and then occupies the
orchestrator's context for the rest of the session.

The common thread is **judgement**. Which agent, how risky, is it finished.

Judgement is the expensive part of orchestration, and you cannot afford to spend
a frontier model on it for every task.

## The Solution

Split the work three ways, and give each part to whatever is cheapest and safest
at it.

> **The probe observes. Jev judges. The chosen agent generates.**

| Script | Job | Role |
| --- | --- | --- |
| `skills/helm/scripts/probe.py` | Finds installed agents, auth state, hardware, and fixes | Observes |
| `skills/helm/scripts/route.py` | Chooses the best available agent for a task | Asks Jev |
| `skills/helm/scripts/supervise.py` | Dispatches a worker and judges whether it finished | Asks Jev |

The probe reads the machine and calls no models.

[TypeSafe Jev](https://typesafe.ai) is a non-generative **System One** model: it
returns typed probabilistic decisions rather than text. It makes every
judgement call here, for about **$0.0001** each.

Only then does a real coding agent write code.

The keystone is that the answer space handed to Jev is built from what the probe
found. Recommending an agent you do not have is *structurally impossible*, not
merely unlikely.

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
| `agent` | cursor-agent @ 0.97 | Picked the semantic indexer over Claude (0.02), not a close call |
| `context_breadth` | 2.68 | Repo-wide, so `parallel` mode was injected into the command |
| `task_kind` | refactor | Selected the find-all-call-sites-first preamble |
| `needs_human` | 0.62 | Crossed the 0.5 gate, so `RECOMMEND` rather than auto-execute |
| `blast_radius` | 1.98 | Under the 2.0 ceiling, so this gate stayed quiet |

One request, about a second, roughly $0.0001.

The prompt handed to `cursor-agent` was assembled from those metrics rather than
passed through unchanged.

## Install

Helm is one skill in the agent-neutral `SKILL.md` format, so it installs
natively wherever you already work.

Pick your host. The setup step at the end is the same for all of them.

### Claude Code

This repository doubles as its own plugin marketplace, so there is nothing to
clone:

```bash
/plugin marketplace add Jimuelle07/Helm
/plugin install helm@helm
```

From a terminal instead: `claude plugin marketplace add Jimuelle07/Helm`, then
`claude plugin install helm@helm`.

Later: `claude plugin update helm@helm`, `claude plugin marketplace update helm`,
`claude plugin uninstall helm@helm`.

### Gemini CLI

The repository is also a Gemini CLI extension, and the skill under `skills/`
comes with it.

```bash
gemini extensions install https://github.com/Jimuelle07/Helm
```

Then `gemini extensions list` / `update helm` / `uninstall helm`.

### Codex, Cursor, OpenCode, and 80+ others

[`npx skills`](https://github.com/vercel-labs/skills) is a package manager for
Agent Skills that knows where each agent keeps its own. It reads this repository
directly, with no clone:

```bash
npx skills add Jimuelle07/Helm            # choose agents interactively
npx skills add Jimuelle07/Helm -g -a '*'  # globally, into every agent it finds
npx skills add Jimuelle07/Helm --list     # just show what the repo contains
```

`-a` takes agent ids (`claude-code`, `opencode`, and so on). `-g` installs for
your user rather than the current project.

Afterwards: `npx skills list`, `npx skills update helm`, `npx skills remove helm`.

### From a clone, into any skills directory

These agents all read Agent Skills off disk, so installing means putting
`skills/helm/` where the agent looks.

`install.sh` does that by symlink, so one `git pull` updates every agent at once:

```bash
git clone https://github.com/Jimuelle07/Helm.git
cd Helm
./install.sh                  # ~/.agents/skills, the shared location
./install.sh codex cursor     # or each agent's own directory as well
```

On Windows use `.\install.ps1`. It takes the same arguments and links with a
junction, so it needs no administrator rights and no developer mode.

| Argument | Installs to | Read by |
| --- | --- | --- |
| *(none)* | `~/.agents/skills/helm` | Codex, Cursor, OpenCode, and other Agent Skills hosts |
| `codex` | `$CODEX_HOME/skills/helm` | Codex CLI |
| `cursor` | `~/.cursor/skills/helm` | Cursor |
| `opencode` | `~/.config/opencode/skills/helm` | OpenCode |
| `claude` | `~/.claude/skills/helm` | Claude Code, without going through the plugin |
| `project` | `./.agents/skills/helm` | Any of the above, scoped to one repository |

`--copy` copies instead of linking, `--list` shows where Helm is installed, and
`--uninstall` removes it.

For an agent on none of these paths, copy `skills/helm/` into whatever directory
it scans. The folder is self-contained, and nothing in Helm depends on having
been installed at all.

### Requirements

Python 3.10+ on `PATH`, and a TypeSafe API key. Nothing is installed into your
Python environment, because the scripts are standard library only.

## Using It

### 1. Set up Jev, once per user

Jev is the decision layer, not an accelerator, so this is a prerequisite rather
than a nicety.

The skill deliberately ships with no key. It is meant to be shared across a
team, and each person brings their own TypeSafe account, the same way they would
with `gh auth login`.

Get a key from your TypeSafe account, then:

```bash
python skills/helm/scripts/route.py --set-api-key apikey_...   # probe.py takes it too
python skills/helm/scripts/probe.py --check-jev                # exit 0 = Helm can route
```

Installed as a skill, you can skip the paths and just say *"set up Helm's
TypeSafe key: `apikey_...`"*. The agent knows where its own copy lives.

The key lands in `~/.cache/helm/credentials.json`, owner-only on POSIX. That
sits outside the skill, so it survives updates and is shared by every agent you
installed Helm into. It is only ever echoed back masked, as `apike...bf32`.

`--clear-api-key` removes it. `TYPESAFE_API_KEY` in the environment always takes
precedence, which is useful for CI or a temporary override.

Run `--check-jev` rather than trusting the key on sight. It makes one real
inference call, so it catches a key that lists models fine but fails at routing
time.

### 2. The normal way: just ask

Once Helm is installed you never name it. Its description is written so that the
questions you would already ask pull it in:

| You say | Helm runs | You get |
| --- | --- | --- |
| *"what coding agents do I have?"* | `probe.py` | Inventory, auth state, fixes |
| *"which agent should do this refactor?"* | `route.py` | A choice, a command, the gates |
| *"have codex fix the failing parser test"* | `supervise.py` | A verdict, not a transcript |

Default behaviour is to recommend, not to run. The agent should show you the
command and the reason it picked that tool. It only dispatches when you ask it
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
| `probe.py` | `--refresh` bypasses the 24h cache, `--json` for the raw registry, `--no-verify-auth` skips login checks, `--check-jev` |
| `route.py` | `--json` full verdict, `--execute` runs it if every gate passes, `--timeout` (default 900), `--watch`, `--refresh` |
| `supervise.py` | `--watch` stops a run that is looping or waiting, `--timeout`, `--retry N` (default 1), `--signals-file`, `--no-modes` |

### 4. Chaining a decision into a dispatch

The task-tuned flags come from Jev's metrics, so hand the verdict to the
supervisor rather than re-typing the command:

```bash
python skills/helm/scripts/route.py "..." --json > decision.json
python skills/helm/scripts/supervise.py claude "..." --signals-file decision.json
```

`route.py --execute` does exactly this in one step.

Without signals, the supervisor dispatches the card's plain base contract. That
is deliberate: nothing here should widen an agent's permissions on the strength
of a metric nobody supplied. `--no-modes` forces the base contract even when
signals exist.

### Exit codes

| Command | Codes |
| --- | --- |
| `probe.py --check-jev` | `0` can route, `1` cannot |
| `route.py` | `0` ok, `1` no routable agents, `2` no API key, `3` gates blocked `--execute`, `4` Jev unreachable |
| `supervise.py` | `0` done or uncertain, `1` needs attention, `2` no API key |

## How It Works

### 1. Probe: observe, without calling a model

`probe.py` inventories the machine. Hardware, every installed agent with its
version, and a one-line *competence* from its [capability
card](skills/helm/references/capability-cards.md). Results cache for 24 hours.

It also asks each CLI whether it is logged in, and the rule there is
deliberately asymmetric.

*"We could not find a credential"* never blocks, because tokens live in
keychains no probe can enumerate. Only *"the tool's own status command says it
is logged out"* removes an agent from routing:

```text
INSTALLED BUT NOT AUTHENTICATED (1)  <- log in to use these
  codex          Not logged in.
                 fix: codex login
```

### 2. Jev: judge, in one typed request

`route.py` hands Jev the task plus the registry, and asks **seven questions,
evaluated independently and in parallel in a single request**:

| Question | Type | What it decides |
| --- | --- | --- |
| `agent` | Choice (**dynamic**) | Which installed agent takes this task |
| `task_kind` | Choice (10) | scaffold / feature / bugfix / refactor / test / docs / ... |
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

Jev cannot return a value outside that space. So *"only recommend what is
installed"* becomes a property of the schema, rather than an instruction you
hope a model respects.

And because Jev returns a distribution rather than a pick, low confidence is
real information. It usually means a genuine near-tie, which trips a gate and
downgrades the run to a recommendation.

Measured against `jev-1.13.0`: **0.7 s, 2,149 input tokens, $0.00009**. Input is
$0.042 per million tokens, and output is billed at zero.

That price is the point. It is what lets the control plane judge *every* task
instead of only the ones that look hard.

The verdict's `mode` is the decision:

| Mode | Meaning |
| --- | --- |
| `auto` | Every gate passed, safe to run |
| `recommend` | Good agent found, but a gate is unmet. Show the command, let the user decide |
| `clarify` | Too vague to hand off. Ask the one question that resolves it |
| `escalate` | No installed agent fits |

### 3. Dispatch: the command is built from the metrics

Jev's numbers are translated into that agent's own flags and preamble before the
command is printed.

A high `blast_radius` runs the change in a git worktree. A wide
`context_breadth` injects `parallel`. A `review` task gets
`gemini --approval-mode plan` and literally cannot write.

Five modes and four rules, in
[`dispatch-modes.md`](skills/helm/references/dispatch-modes.md).

### 4. Supervise: judge the result, not the exit code

`supervise.py` dispatches the worker, then sends the transcript to Jev instead
of to your context. The transcript goes to disk, and you get a few hundred
bytes:

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
the supervisor retries once by default with that agent's own cure, such as
Aider's `/undo`, or an apply-the-edits-now instruction.

`--watch` polls a run in flight and stops it early if Jev sees it looping or
waiting for input. A `timeout` never auto-retries, because the tree may be
half-modified.

### When Jev cannot be reached, Helm stops

There is no second scorer.

| Situation | What happens |
| --- | --- |
| No key | `route.py` and `supervise.py` print setup instructions, exit `2` |
| API unreachable | `route.py` reports the API error and exits `4`, with no route |
| Jev dies after the worker ran | Reported unjudged, with the transcript path, and never auto-retried |

This is deliberate. The questions Helm asks are exactly the ones local
heuristics answer badly: telling "wrote the code" from "wrote *about* the code"
needs a reader, not a keyword scan.

A fallback that cannot tell would be confidently wrong on precisely the cases
the tool exists to catch. Every decision is logged to
`~/.cache/helm/decisions.jsonl`, so the thresholds can be fitted against real
traces later.

## Documentation

| File | Purpose |
| --- | --- |
| [`walkthrough.md`](skills/helm/references/walkthrough.md) | Start here: install to end-to-end, with real output |
| [`SKILL.md`](skills/helm/SKILL.md) | Skill workflow and result-presentation rules |
| [`capability-cards.md`](skills/helm/references/capability-cards.md) | Card schema, and writing competence lines that discriminate |
| [`jev-decision-layer.md`](skills/helm/references/jev-decision-layer.md) | Both question sets, the typed primitives, the API contract |
| [`dispatch-modes.md`](skills/helm/references/dispatch-modes.md) | How a metric becomes a CLI flag: five modes, four rules |
| [`calibration.md`](skills/helm/references/calibration.md) | Thresholds, and how to fit them against recorded traces |

Teaching Helm a new agent is one JSON file in `skills/helm/scripts/cards/`: the
binary names, how to invoke it headlessly, how to check its login, and a
contrastive `competence` line. That line is what Jev actually reads, so treat it
as a routing parameter rather than documentation.

## Honest Limitations

- **Every threshold is uncalibrated.** They are conservative starting points,
  and decisions log to `~/.cache/helm/decisions.jsonl` so they can be fitted.
- **The Jev wire contract is verified as of 2026-09-20 with `jev-1.13.0`.**
  `GET /v1/models` lists only the `jev-latest` and `jev-preview` aliases, so the
  concrete version cannot be discovered there. Traces record the resolved
  version, so drift stays detectable.
- **Some capability cards are best effort.** Cards whose invocation or auth
  commands have not been verified are flagged at runtime.
- **Jev is a hard dependency.** With the decision layer unreachable, there is
  nothing left to orchestrate with but an inventory.

## FAQ

**Is this Helm, the Kubernetes package manager?**
No, and there is no relation. Never use it for charts, `helm install`, releases
or clusters. It is a name collision, nothing more.

**Which coding agents can it route to?**
Claude Code, Codex, Cursor Agent, Gemini CLI, Aider, OpenCode, GitHub Copilot,
Amp, Droid, Crush, Goose, Qwen Code, and Ollama-hosted local models. One
capability card each.

**Which agents can run Helm itself?**
Any that reads Agent Skills: Claude Code, Codex, Cursor, OpenCode, Gemini CLI
and 80+ more. See [Install](#install).

**Do I need a TypeSafe account?**
Yes. Jev is the decision layer, not an optional accelerator, and there is no
fallback scorer. Routing costs about $0.0001 per task.

## Credits

Helm was built by **[Jimuelle Patron](https://github.com/Jimuelle07)** and is
released under the [MIT license](LICENSE). The decision layer is
[TypeSafe Jev](https://typesafe.ai).

If Helm routed something well, or badly, that disagreement is the useful signal.
[Open an issue](https://github.com/Jimuelle07/Helm/issues).
