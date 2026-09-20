# agent-router

**Your coding agent can't see the other coding agents installed next to it.** This skill
fixes that — and makes sure it never hands work to one that isn't logged in.

If you have `claude`, `codex`, `cursor-agent`, `gemini`, `aider`, `opencode` and `copilot`
on your machine, that's seven agents fronting four model families, and not one of them can
name the other six. An LLM only knows the tools in its prompt. Everything else is invisible
until something fails.

## What it does

```
probe.py      →  what's installed, what's logged in, what this machine can run
route.py      →  which of those should take this task           (Jev decides)
supervise.py  →  dispatch it, and tell me when it's actually done (Jev decides)
```

[TypeSafe Jev](https://typesafe.ai) is a *System One* model: it emits no text, only typed
decisions — 70–500ms, $0.042 per million input tokens, output free. Cheap enough to run on
every task instead of only the ones that look hard.

## Three problems it solves

**1. Recommending a tool you don't have.** The list of options handed to Jev is built from
what the probe actually found, and Jev cannot return a value outside its declared answer
space. So suggesting an uninstalled agent isn't unlikely — it's *structurally impossible*.

**2. Routing to a CLI that was never logged in.** Installing an agent and authenticating it
are separate acts, and people routinely do only the first. The CLI looks fine to a
`--version` check and then fails on its first model call. `probe.py` asks each tool's own
status command, and anything that reports logged out is pulled from routing and shown with
its one-line fix:

```
INSTALLED BUT NOT AUTHENTICATED (1)  <- log in to use these
  codex          Not logged in.
                 fix: codex login
```

The rule is asymmetric on purpose. *We couldn't find a credential* never blocks — tokens
live in keychains no probe can read. Only *the tool said it's logged out* does.

**3. Burning tokens to ask "is it done yet?"** An agent transcript runs to tens of
thousands of tokens, and reading one to check completion costs real money and permanently
fills your orchestrator's context with build noise. Jev reads it instead, for a fraction of
a cent, and hands back a few hundred bytes:

```
DONE -- codex (completed)
  Edited src/parser.py and ran the suite: 14 passed
  exit 0 after 47.2s | judged by jev
  next: accept
  full transcript: /tmp/agent-router-runs/codex-1758.log
```

This catches what an exit code can't. Agents exit 0 after describing work they never did
(`no_op`), and exit 0 after asking a question into a headless void (`stuck`). `rc == 0`
reads both as success.

## Install

Nothing to install. Python 3.10+, standard library only — tested on 3.12, 3.14 and 3.15.
Works on Windows, macOS and Linux.

```bash
python scripts/probe.py --repo .
python scripts/route.py "add retry logic to the payment client" --repo .
```

To enable Jev, add your own TypeSafe key once:

```bash
python scripts/route.py --set-api-key sk-...
```

**Without a key it still works.** Routing falls back to a deterministic scorer over the same
capability cards, with confidence hard-capped at 0.5 — below every accept threshold, so a
degraded judge can recommend but never act unattended. It always tells you which brain
answered.

## Teaching it a new agent

Capability cards in `scripts/cards/*.json` are the declared knowledge that makes this work
without invoking anything. Add one file to support a new CLI:

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

The `competence` line is what Jev actually reads, so it's a tuned parameter, not
documentation — write it to *discriminate* against the other cards, not to describe the
tool in isolation. See `references/capability-cards.md`.

## Docs

| File | What's in it |
|---|---|
| `SKILL.md` | The workflow, and how to present results |
| `references/capability-cards.md` | Card schema; writing a competence line that routes well |
| `references/jev-decision-layer.md` | Every question asked, and why each one is independent |
| `references/calibration.md` | The thresholds, why they're guesses, and how to fit them |

## Honest limitations

- **Every threshold is uncalibrated.** They're conservative starting points, labelled as
  such everywhere they appear. Decisions log to `~/.cache/agent-router/decisions.jsonl`
  from day one so they *can* be fitted later.
- **The raw-HTTP Jev encoding is inferred** from the documented SDK surface, not verified
  against a live endpoint. Where `typesafe-sdk` is installed it's used instead and is
  authoritative; either way a failure degrades to the fallback rather than breaking.
- **Six of the 13 cards are unverified.** The seven I could test have their invocation and
  auth commands confirmed against each tool's `--help`; the rest are best-effort and are
  flagged as such at runtime.
