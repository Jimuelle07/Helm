---
name: helm
description: >
  Discovers which coding-agent CLIs are actually installed AND logged in on this machine
  (Claude Code, Codex, Cursor Agent, Gemini, Aider, OpenCode, Copilot, Ollama and others)
  along with its CPU/RAM/VRAM, then uses TypeSafe Jev as a typed decision layer to pick the
  best available agent, dispatch the task, and judge whether the worker actually finished.
  Use this whenever the user asks which model or agent should handle something, wants to
  delegate or route work to another CLI agent, asks what agents or models are available on
  their machine, compares agents ("is Claude or Codex better for this refactor?"), asks
  whether a task can run on a local model, hits an agent CLI that is installed but failing
  to call its model, or wants a build orchestrated across several agents. Also use it before
  starting a substantial build when picking the wrong tool would be expensive — even if the
  user never names an agent — because a coding agent otherwise cannot see the sibling agents
  installed right next to it. This skill is about steering AI coding agents; it has nothing
  to do with Helm the Kubernetes package manager, so do not use it for charts, `helm install`,
  Kubernetes releases, or cluster deployment.
---

# Helm

*Taking the helm of the agents already on your machine.*

## Why this exists

An LLM does not know its own tools unless they are called. A coding agent learns what it
can do from a tool list in its prompt, so the six other agent CLIs sitting in the same
`PATH` — each with a different model, price, context window, and speciality — are simply
invisible to it. Worse, when an agent does stray outside its competence, it finds out by
*failing*: attempt, burn tokens, retry, apologise. The knowledge is produced after paying
for it, then thrown away when the session ends.

This skill fixes that by splitting the problem into three jobs and giving each to the
thing that is cheapest and safest at it:

> **The probe observes. Jev judges. The chosen agent generates.**

The probe reads the machine (no model calls). Jev — a System One model that returns typed
decisions rather than text — picks from an answer space built *from what the probe found*.
Only then does a real coding agent write code.

The keystone is that last point. The list of options handed to Jev is constructed from the
installed agents, so recommending something that is not on the machine is **structurally
impossible**, not merely unlikely. That is the whole reason to use a typed decision model
here instead of asking an LLM to please only suggest installed tools.

## Setting up Jev (once per user)

This skill is shared across a team, so the Jev API key is never bundled with it — each
person authenticates with their own TypeSafe account, the same way they would with `gh
auth login` or a database credential. `probe.py` reports whether a key is currently
configured and, if not, tells you the exact command to fix it.

If the user wants live Jev judgement and `probe.py` reports no key configured, ask them
for their TypeSafe API key (from `docs.typesafe.ai` / their TypeSafe account) and run:

```bash
python scripts/route.py --set-api-key apikey_...
```

This stores the key locally at `~/.cache/helm/credentials.json` (owner-only
permissions on POSIX) and it is picked up on every future call — nothing else to
configure. `--clear-api-key` removes it. Confirm it works with one live request:

```bash
python scripts/probe.py --check-jev
``` A `TYPESAFE_API_KEY` environment variable, if
set, always takes precedence over the stored key, which is useful for CI or a temporary
override without disturbing what is stored.

**Never print, log, or echo the key itself** — confirmation messages only ever show a
masked form (`apike...bf32`). Without a key, everything still works: routing falls back to
a deterministic scorer whose confidence is hard-capped at 0.5, so it can recommend but
never auto-execute. Tell the user this plainly rather than silently degrading.

## The workflow

All paths are relative to this skill's directory. Run steps 1 and 2 in order; step 3 is
opt-in.

### 1. See what the machine has

```bash
python scripts/probe.py --repo .
```

Prints hardware, every installed agent with its version and one-line competence, which
ones are routable, and whether the Jev decision layer is available. Results cache for 24
hours (`--refresh` to force a re-probe; `--json` for the raw registry).

Run this whenever the user asks what they have available, or before any routing decision.
It calls no models, so it is cheap and safe to run eagerly.

**It also asks each CLI whether it is logged in.** Installing a coding agent and
authenticating it are separate acts, and people routinely do the first without the second —
the CLI then looks perfectly healthy until the moment it tries to call a model. Agents that
their own status command reports as logged out appear under `INSTALLED BUT NOT
AUTHENTICATED` with the exact fix command, and are excluded from routing so no task is
handed to an agent that cannot run it.

If you see that block, tell the user which agent and give them the one-line fix. They log
in, and the next run picks it up immediately — logged-out agents are re-checked on every
invocation rather than waiting out the cache. Use `--no-verify-auth` to skip the check when
you only need a fast inventory.

### 2. Route a task

```bash
python scripts/route.py "add retry logic with backoff to the payment client"
```

Feeds the task plus the registry to Jev, applies the thresholds, and prints a decision:
the chosen agent, the exact command to run it, the signals behind the call, and — when it
declines to auto-execute — precisely which gate stopped it.

Useful flags: `--repo PATH` for repository context, `--json` for the full verdict,
`--execute` to actually run the agent.

**The printed command is tuned to the task, not generic.** Jev's metrics are translated
into that agent's own flags and keywords before the command is shown:

```
RECOMMEND -> Cursor Agent (cursor-agent)
  command:
    cursor-agent -w -p refactor the payment client to use the new retry helper ...
  modes injected: sandbox
```

`blast_radius` came back at 2.5, so the change runs in a git worktree rather than the live
tree. A `review` task gets `gemini --approval-mode plan` and literally cannot write; a
`scaffold` task gets an architecture-first preamble; a wide `refactor` gets told to fan out
across sub-agents. The four rules and their thresholds are in
`references/dispatch-modes.md`.

Two things to tell the user when they come up:

- `modes this agent cannot express: sandbox` means the chosen CLI has no way to do what the
  metrics asked for. It is not an error, but it is worth saying out loud on a high-blast
  task — offer a worktree, or a different agent.
- `caveat: injected flags for X are unverified` means that flag came from a cheat sheet
  rather than from the CLI's own `--help`. Seven of the thirteen cards were written on a
  machine where those tools were not installed.

### 3. Hand off, and let Jev tell you when it is done

Default behaviour is to recommend, not to run. Prefer showing the user the command and
letting them decide. Reach for `--execute` only when the user has clearly asked you to
just do it, and even then the gates in `scripts/route.py` can still refuse.

When you do dispatch, go through the supervisor rather than running the agent yourself:

```bash
python scripts/supervise.py codex "fix the failing test in src/parser.py"
python scripts/supervise.py claude "..." --watch --timeout 900
```

To get the same task-tuned flags when driving the supervisor directly, hand it the routing
decision:

```bash
python scripts/route.py "..." --json > /tmp/decision.json
python scripts/supervise.py claude "..." --signals-file /tmp/decision.json
```

`--execute` on `route.py` does this for you. Without signals the supervisor dispatches the
card's plain base contract — which is the old behaviour, deliberately: nothing here widens
an agent's permissions on the strength of a metric nobody supplied. `--no-modes` forces
that base contract even when signals are present.

**Do not read the worker's transcript to decide whether it finished.** That is the single
most expensive thing you can do here: agent transcripts run to tens of thousands of tokens,
and reading one costs real money *and* permanently fills your context with build noise you
then carry for the rest of the session.

`supervise.py` hands the transcript to Jev instead — input is $0.042 per million tokens and
output is free, so judging a 50k-token run costs a fraction of a cent. What comes back to
you is a few hundred bytes:

```
DONE -- codex (completed)
  Edited src/parser.py and ran the suite: 14 passed
  confidence 0.88 | satisfied 0.91 | needs_human 0.08 | awaiting_input 0.02
  exit 0 after 47.2s | judged by jev
  next: accept
  full transcript: /tmp/helm-runs/codex-1758...log
```

Act on `next`, and only open the transcript if it says `review` or you have a specific
reason. That choice — the log being somewhere you *can* look rather than something that
arrives unbidden — is where the saving actually comes from.

`--watch` polls the run while it is in flight and stops it early if Jev sees it going in
circles or waiting for input. That second case is worth the flag on its own: an agent
running headless sometimes asks a clarifying question and then waits for an answer that
can never arrive, and without `--watch` it burns the entire timeout before anyone notices.

## Reading the output

The `mode` field is the decision:

| Mode | Meaning | What to do |
|---|---|---|
| `auto` | Every gate passed | Safe to offer to run it, or run it if asked |
| `recommend` | A good agent was found, but at least one gate is unmet | Present the choice and the blocking gates; let the user decide |
| `clarify` | The request is too vague to hand off | Ask the user the specific question that resolves it, then re-route |
| `escalate` | No installed agent fits | Say so plainly and suggest what would need installing |

Always tell the user **which brain judged it**. The `source` field is either `jev` or
`fallback`, and a fallback verdict is a materially weaker claim — see below.

For a dispatched run, `supervise.py` returns `outcome` and `next`:

| Outcome | Meaning | `next` |
|---|---|---|
| `done` | Task carried out, high confidence | `accept` |
| `uncertain` | Looks finished but the judge is not confident | `review` |
| `incomplete` | Claimed done, but the task was not actually satisfied | `review` |
| `no_op` | The agent described the work instead of doing it | `retry` |
| `stuck` | Waiting on input that cannot arrive headlessly | `ask_user` |
| `failed` | Errored out | `retry` |
| `timeout` | Killed at the deadline; work may be half-applied | `escalate` |
| `error` | Precondition failed — not installed, not logged in, no contract | `escalate` |

`no_op` and `stuck` are the two that matter most, because an exit code cannot see either.
Agents exit 0 after announcing what they *would* do, and exit 0 after asking a question
into the void. If you were checking `rc == 0`, both would read as success.

### Recovery

`no_op`, `stuck` and `failed` are diagnoses, not dead ends, and most agents ship the cure.
The supervisor retries once by default, injecting that agent's own recovery move — Aider's
`/undo` before a retry, an explicit "you are headless, nobody can answer you" preamble for
a stuck run, an apply-the-edits-now instruction for a no-op. The verdict then carries an
`attempts` list:

```
DONE -- claude (completed)
  note: recovery: no_op: re-dispatch with an explicit apply-the-edits instruction
  note: recovered and retried 1 time(s); outcomes: no_op -> done
```

`--retry 0` disables it; `--retry 2` allows two. All attempts share the one `--timeout`
budget, so a retry never doubles the wall clock you asked for.

Two cases deliberately never retry. A `timeout` may have left the tree half-modified, and
re-dispatching onto unknown partial state turns one bad run into two. And a verdict from
the **heuristic fallback** never triggers a retry at all — the fallback cannot tell a terse
success from a no-op, so acting on it would risk re-running work that already succeeded.
You will see `not retrying: verdict came from the fallback judge` when that happens.

## When Jev is unavailable

If no API key is configured (see setup above) or the API is unreachable, `route.py` falls
back to a deterministic scorer over the same capability cards. This is a real, supported
path, not an error state — but its confidence is hard-capped at 0.5, which by design means
a fallback verdict can never reach `auto` mode.

The reasoning: a degraded judge is allowed to *suggest*, never to *act unattended*. When
you report a fallback result, say that Jev was unavailable and that the recommendation is
heuristic. Do not present it with the same confidence as a Jev verdict.

## Presenting a recommendation

Lead with the choice and the reason, keep the machinery in the background, and be honest
about uncertainty. Something like:

> For a repo-wide rename, `cursor-agent` is the better fit here — it indexes the
> repository semantically, so it can find every call site before editing. Claude Code is
> the runner-up if you would rather have it reason through the edge cases.
>
> ```
> cursor-agent -p "rename Client to ApiClient across the repo and update all call sites"
> ```
>
> Jev was unavailable, so this came from the heuristic scorer — worth a sanity check.

Avoid dumping raw JSON at the user unless they asked for it. The signals exist to justify
the call, not to be recited.

## Adding or correcting an agent

Capability cards in `scripts/cards/*.json` are the declared knowledge that makes this work
without invoking anything. To teach the router a new agent, add one file. To fix a bad
routing decision, the card text is usually the right thing to change — the `competence`
line is what Jev actually reads, so it is a tuned parameter, not documentation.

See `references/capability-cards.md` for the schema and how to write a `competence` line
that discriminates well.

## Further reading

Load these only when the task calls for them:

- `references/walkthrough.md` — full install-to-end-to-end example with real output; read this
  first if you are unsure how the pieces fit together
- `references/capability-cards.md` — card schema, writing good competence lines, adding an agent
- `references/dispatch-modes.md` — how a Jev metric becomes a CLI flag: the five modes, the
  four rules, the recovery table, and which flags are verified
- `references/jev-decision-layer.md` — the question set, why each question exists, Jev API details
- `references/calibration.md` — the thresholds, why they are currently guesses, and how to fit them
  against recorded traces

## Three things worth knowing

**Undetected credentials are not absent credentials.** The probe distinguishes two things
that are easy to conflate. *We could not find a credential* means nothing — tokens live in
keychains and browser sessions no probe can enumerate — so it never blocks. *The tool's own
status command said it is logged out* is authoritative, and does block. Only the second is
a reason to route elsewhere; mention the first as a caveat and move on.

**Every decision is logged** to `~/.cache/helm/decisions.jsonl`, because the
thresholds in `route.py` and `supervise.py` are honest guesses until there are real traces
to fit them against. If the user disagrees with a routing call, that disagreement is the
valuable signal — note it, and point them at `references/calibration.md`.

**Nothing here auto-executes on a weak signal.** Whenever Jev is unavailable, both the
router and the supervisor cap their confidence below their own accept thresholds, so a
degraded judge can recommend but never wave work through. If you find yourself about to say
"it's done" on a `fallback` verdict, say "it looks done, but Jev wasn't available" instead.
