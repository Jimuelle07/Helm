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

1. **Observe** — `probe.py` reads the hardware, discovers installed agent CLIs, and asks each
   one whether it is actually logged in. No model calls.
2. **Judge** — Jev answers seven typed questions about the task in one parallel request.
3. **Compose** — thresholds and gates turn typed answers into a route. Pure, and fully unit-tested.
4. **Act** — `supervise.py` dispatches the chosen agent and lets Jev judge whether it finished,
   so the orchestrating model never has to read the worker's transcript.

The keystone: the answer space handed to Jev is built **from what the probe found**, so
recommending an agent you do not have is structurally impossible rather than merely unlikely.

## Quick start

No dependencies beyond Python 3.10+.

```bash
# What does this machine actually have?
python skills/helm/scripts/probe.py --repo .

# Which agent should handle this?
python skills/helm/scripts/route.py "add retry logic to the payment client" --repo .

# Run the tests
python -m unittest discover -s tests
```

Every user configures their own Jev access — the key is never bundled with the skill:

```bash
python skills/helm/scripts/route.py --set-api-key apikey_...   # once, per user
python skills/helm/scripts/route.py --clear-api-key        # to remove it
```

Stored locally at `~/.cache/helm/credentials.json` (a `TYPESAFE_API_KEY` env var
always takes precedence if set). Without a key, routing falls back to a deterministic
scorer whose confidence is capped at 0.5 — a degraded judge may suggest, never act
unattended.

## Layout

| Path | What it is |
|---|---|
| `skills/helm/` | **Helm** — the distributable agent skill |
| `skills/helm/SKILL.md` | Workflow and how to present a recommendation |
| `skills/helm/scripts/probe.py` | Hardware + PATHEXT-aware agent discovery |
| `skills/helm/scripts/route.py` | Routing question set, thresholds, fallback scorer |
| `skills/helm/scripts/supervise.py` | Dispatch, Jev-judged completion, recovery loop |
| `skills/helm/scripts/dispatch.py` | Jev metrics -> agent-native flags and keywords |
| `skills/helm/scripts/jev.py` | Shared Jev client |
| `skills/helm/scripts/keystore.py` | Per-user API key storage |
| `skills/helm/scripts/cards/` | Capability cards — declared knowledge, one file per agent |
| `skills/helm/references/` | Card schema, dispatch modes, Jev decision layer, calibration |
| `tests/` | 212 tests; pure layers exhaustively, plus real dispatch |

## Docs

- [`skills/helm/references/walkthrough.md`](skills/helm/references/walkthrough.md) — **start here**: install to end-to-end with real output
- [`docs/idea.md`](docs/idea.md) — the thesis: the problem, the bet, why Jev is the right shape
- [`docs/system-design.md`](docs/system-design.md) — the build spec: layers, questions, thresholds
- [`docs/context.md`](docs/context.md) — research notes on what Jev is and is not
- [`docs/machine-profile.md`](docs/machine-profile.md) — generated snapshot of the development box

## Three failures it exists to prevent

**Routing to a CLI that was never logged in.** Installing an agent and authenticating it are
separate acts, and people routinely do only the first. The CLI passes a `--version` check and
then fails on its first model call. `probe.py` asks each tool's own status command; anything
that reports logged out is pulled from routing and shown with its fix. The rule is asymmetric
on purpose — *we couldn't find a credential* never blocks (tokens live in keychains no probe
can read); only *the tool said it's logged out* does.

**Handing every agent the same generic command.** A coding CLI's flags are where its
safety lives — worktrees, sandboxes, plan modes, permission gates — and a router that
ignores them throws that away. `dispatch.py` translates Jev's metrics into each agent's own
vocabulary: a high blast radius becomes `-w`, a `review` task becomes `--approval-mode
plan` and cannot write, a wide refactor gets told to fan out. Metrics nobody supplied
change nothing, which is what keeps the layer additive rather than a new way to run `--yolo`
by accident.

**Spending orchestrator tokens on "is it done yet?"** An agent transcript runs to tens of
thousands of tokens. Jev reads it for a fraction of a cent and returns a few hundred bytes;
the transcript goes to disk. This also catches what an exit code cannot — agents exit 0 after
describing work they never did, and exit 0 after asking a question into a headless void.

## Status

Working end to end, with one honest gap: **every threshold is uncalibrated.** They are conservative
starting points, not fitted values, and they are labelled as such everywhere they appear. Decisions
are logged to `~/.cache/helm/decisions.jsonl` from day one so they *can* be fitted —
see [`references/calibration.md`](skills/helm/references/calibration.md).

The Jev wire contract is **verified** against the live endpoint (2026-09-20, `jev-1.13.0`): Bearer
auth, all three question types, and the `jev-1.13.0` pin, which the API validates rather than
silently ignoring. Confirm your own setup with `probe.py --check-jev`. A routing decision costs
about $0.00009.
