---
name: agent-router
description: >
  Discovers which coding-agent CLIs and models are actually installed on this machine
  (Claude Code, Codex, Cursor Agent, Gemini, Aider, OpenCode, Copilot, Ollama and others)
  along with its CPU/RAM/VRAM, then uses TypeSafe Jev as a typed decision layer to pick
  the best available agent for a task and hand it off. Use this whenever the user asks
  which model or agent should handle something, wants to delegate or route work to another
  CLI agent, asks what agents or models are available on their machine, compares agents
  ("is Claude or Codex better for this refactor?"), asks whether a task can run on a local
  model, or wants a build orchestrated across several agents. Also use it before starting
  a substantial build when picking the wrong tool would be expensive — even if the user
  never names an agent — because a coding agent otherwise cannot see the sibling agents
  installed right next to it.
---

# Agent Router

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
python scripts/route.py --set-api-key sk-...
```

This stores the key locally at `~/.cache/agent-router/credentials.json` (owner-only
permissions on POSIX) and it is picked up on every future call — nothing else to
configure. `--clear-api-key` removes it. A `TYPESAFE_API_KEY` environment variable, if
set, always takes precedence over the stored key, which is useful for CI or a temporary
override without disturbing what is stored.

**Never print, log, or echo the key itself** — confirmation messages only ever show a
masked form (`sk-ab...cd12`). Without a key, everything still works: routing falls back to
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

### 2. Route a task

```bash
python scripts/route.py "add retry logic with backoff to the payment client"
```

Feeds the task plus the registry to Jev, applies the thresholds, and prints a decision:
the chosen agent, the exact command to run it, the signals behind the call, and — when it
declines to auto-execute — precisely which gate stopped it.

Useful flags: `--repo PATH` for repository context, `--json` for the full verdict,
`--execute` to actually run the agent.

### 3. Hand off (only when it is warranted)

Default behaviour is to recommend, not to run. Prefer showing the user the command and
letting them decide. Reach for `--execute` only when the user has clearly asked you to
just do it, and even then the gates in `scripts/route.py` can still refuse.

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

- `references/capability-cards.md` — card schema, writing good competence lines, adding an agent
- `references/jev-decision-layer.md` — the question set, why each question exists, Jev API details
- `references/calibration.md` — the thresholds, why they are currently guesses, and how to fit them
  against recorded traces

## Two things worth knowing

**Undetected credentials are not absent credentials.** The probe reports when it cannot
find an API key or config for an agent, but it does *not* exclude that agent — plenty of
CLIs keep tokens in an OS keychain or a browser session. Treat it as a caveat to mention,
not a reason to route elsewhere.

**Every decision is logged** to `~/.cache/agent-router/decisions.jsonl`, because the
thresholds in `route.py` are honest guesses until there are real traces to fit them
against. If the user disagrees with a routing call, that disagreement is the valuable
signal — note it, and point them at `references/calibration.md`.
