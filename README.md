# jev-harness

An orchestrator that knows what your machine can actually do, and uses
[TypeSafe Jev](https://typesafe.ai) as a typed decision layer to route each build task to the
best coding agent you have installed.

## The problem

An LLM does not know its own tools unless they are called. A coding agent learns what it can do
from a tool list in its prompt — so the other agent CLIs sitting in the same `PATH`, each with a
different model, price, context window and speciality, are invisible to it. When an agent does
stray outside its competence, it finds out by *failing*, and that knowledge dies with the session.

On the development box there are **seven** headless-capable coding agents installed side by side,
and not one of them can name the other six.

## The approach

> **The probe observes. Jev judges. The chosen agent generates. Code composes and executes.**

1. **Observe** — `probe.py` reads the hardware and discovers installed agent CLIs. No model calls.
2. **Judge** — Jev answers seven typed questions about the task in one parallel request.
3. **Compose** — thresholds and gates turn typed answers into a route. Pure, and fully unit-tested.
4. **Act** — invoke the chosen agent headless, but only if every gate passes.

The keystone: the answer space handed to Jev is built **from what the probe found**, so
recommending an agent you do not have is structurally impossible rather than merely unlikely.

## Quick start

No dependencies beyond Python 3.10+.

```bash
# What does this machine actually have?
python skills/agent-router/scripts/probe.py --repo .

# Which agent should handle this?
python skills/agent-router/scripts/route.py "add retry logic to the payment client" --repo .

# Run the tests
python -m unittest discover -s tests
```

Every user configures their own Jev access — the key is never bundled with the skill:

```bash
python skills/agent-router/scripts/route.py --set-api-key sk-...   # once, per user
python skills/agent-router/scripts/route.py --clear-api-key        # to remove it
```

Stored locally at `~/.cache/agent-router/credentials.json` (a `TYPESAFE_API_KEY` env var
always takes precedence if set). Without a key, routing falls back to a deterministic
scorer whose confidence is capped at 0.5 — a degraded judge may suggest, never act
unattended.

## Layout

| Path | What it is |
|---|---|
| `skills/agent-router/` | The distributable agent skill |
| `skills/agent-router/SKILL.md` | Workflow and how to present a recommendation |
| `skills/agent-router/scripts/probe.py` | Hardware + PATHEXT-aware agent discovery |
| `skills/agent-router/scripts/route.py` | Question set, thresholds, Jev client, fallback, executor |
| `skills/agent-router/scripts/cards/` | Capability cards — declared knowledge, one file per agent |
| `skills/agent-router/references/` | Card schema, Jev decision layer, calibration |
| `tests/test_router.py` | 31 tests over the pure decision layer |

## Docs

- [`docs/idea.md`](docs/idea.md) — the thesis: the problem, the bet, why Jev is the right shape
- [`docs/system-design.md`](docs/system-design.md) — the build spec: layers, questions, thresholds
- [`docs/context.md`](docs/context.md) — research notes on what Jev is and is not
- [`docs/machine-profile.md`](docs/machine-profile.md) — generated snapshot of the development box

## Status

Working end to end, with one honest gap: **every threshold is uncalibrated.** They are conservative
starting points, not fitted values, and they are labelled as such everywhere they appear. Decisions
are logged to `~/.cache/agent-router/decisions.jsonl` from day one so they *can* be fitted —
see [`references/calibration.md`](skills/agent-router/references/calibration.md).

The raw-HTTP encoding of Jev questions is also inferred from the documented SDK surface rather than
verified against a live endpoint, since this machine has no API key. The SDK path is authoritative
where the SDK is installed, and any failure degrades to the fallback scorer rather than breaking.
