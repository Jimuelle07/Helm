# The Jev Decision Layer

Jev is a **System One** model from TypeSafe AI. It is non-generative: it emits no tokens,
prose, or code. You hand it state plus typed questions and it returns typed probabilistic
decisions in a single forward pass.

> Unstructured or structured state in → typed probabilistic decisions out.
>
> **Code calculates. Jev judges. LLMs reason and generate.**

Practical numbers: 70–500 ms end to end, $0.042 per *million* input tokens with output
free, 64k context (32k for state plus the longest question), text only. Cheap enough that
the control plane of an agent harness can run on **every** task rather than only the ones
that look hard — which is exactly the condition under which routing starts paying for
itself.

## The three primitives

All three mix freely in one request. Questions are evaluated **independently and in
parallel** against the same state, so adding a question barely moves latency. Question B
does not see question A's answer.

**Noul** — "is this statement true?" Returns a single probability in [0, 1]. The
probability *is* the uncertainty; there is no separate confidence field.

**Choice** — "pick one of these." Up to 255 options. Returns `choice`, the full
`probabilities` distribution, and `confidence`. Always include a `none`/`other` escape
hatch, or an incomplete taxonomy forces a confident wrong answer.

**Score** — "where on this ordered scale?" 2–10 ordered levels; returns a continuous
`score` that can land *between* levels (e.g. `1.035`), plus `probabilities` and
`confidence`. Levels must be concrete situations — `"no user-visible impact"` beats
`"low"`.

## Our question set

Defined in `scripts/route.py`, which is deliberately the single reviewable surface for
both the questions and the thresholds applied to their answers. Keeping them together is
what makes the policy diffable and calibratable as one unit instead of scattered across
modules.

| Key | Type | Why it exists |
|---|---|---|
| `task_kind` | Choice(10) | Drives the fallback scorer and gives traces a grouping dimension |
| `agent` | Choice(**dynamic**) | The routing decision itself |
| `blast_radius` | Score(4) | How much damage a wrong edit does — gates automation |
| `spec_clarity` | Score(4) | Distinguishes "ask a question" from "hand it off" |
| `context_breadth` | Score(4) | Sanity-checks the agent choice against context class |
| `needs_human` | Noul | Direct approval gate |
| `reversible` | Noul | Whether `git checkout` is a sufficient undo |

### Why these are independent

All seven are answerable from the state alone, which is the real test. `agent` does not
need to *see* `task_kind`'s answer — both read the same intent and registry.

Where a genuine information dependency exists, that is a **serial** second call, not
another question in the same batch. Re-routing after an agent fails is the clear example:
the failure has to be in state before the follow-up question can mean anything.

### The dynamic answer space

```python
criteria = {a["name"]: a["competence"] for a in routable}
criteria["none"] = "No installed agent is a good fit; escalate to the user"
```

Because `criteria` is built from probe output, an uninstalled agent is not a key, and Jev
cannot return a value outside the declared space. "Best model available *on the user's
machine*" becomes a property of the schema rather than an instruction we hope is
respected.

`compose()` re-checks the returned name against the routable set anyway. That path is
unreachable through a Choice by construction; it is defence in depth against a future edit
that widens the answer space.

## The second question set: did the worker finish?

`supervise.py` owns a separate set, asked after (or during) a dispatched run. It exists for
a token reason as much as a quality one: deciding "is this done?" by having the
orchestrating model read the worker's transcript is the most expensive thing in the whole
loop. Transcripts run to tens of thousands of tokens, and reading one both costs money and
permanently fills the orchestrator's context with build noise.

At $0.042 per million input tokens with output free, Jev judges a 50k-token transcript for
roughly a fifth of a cent and returns a few hundred bytes. The transcript goes to disk; the
orchestrator gets the conclusion and a path.

| Key | Type | Why it exists |
|---|---|---|
| `status` | Choice(5) | completed / partial / no_op / blocked_needs_input / failed |
| `task_satisfied` | Noul | the work was *done*, not merely described or planned |
| `needs_human` | Noul | direct escalation signal |
| `awaiting_input` | Noul | the run is waiting on an answer that cannot arrive headlessly |
| `failure_severity` | Score(4) | is the workspace likely left dirty? |

### Why not just check the exit code

Because it is blind in both directions, and the interesting failures are the ones it cannot
see at all. Coding agents exit 0 after announcing what they *would* have done without
touching a file; they exit 0 having asked a clarifying question into a headless void; they
exit non-zero over a lint warning on otherwise finished work. `rc == 0` cannot separate
"wrote the code" from "wrote about the code" -- which is exactly the distinction that
decides whether to accept or retry.

`no_op` and `blocked_needs_input` are in the taxonomy specifically because they are the two
an exit code is structurally incapable of reporting.

### Keeping the transcript inside the budget

Jev allows 32k tokens for state plus the longest question, and agent output can be far
larger. `supervise.excerpt()` keeps a 5k-character head and a 19k-character tail, weighted
to the tail because that is where an agent says what it did, what broke, and what it is
waiting for -- the head is mostly the task echoing back. ANSI escape codes are stripped
first: CLI tools colour their output even when piped, and those codes are pure token waste
that also breaks pattern matching.

### Mid-run polling

`--watch` asks a deliberately narrower pair of questions (`made_progress`, `awaiting_input`)
on an interval while the run is still in flight. Mid-run, those are the only two answers
that can change what we do -- keep waiting, or stop -- so asking the full completion set on
every poll would be spending latency on questions whose answers are not yet meaningful.

This is also a genuine information dependency rather than a fan-out: the poll asks about
progress *since the previous excerpt*, so the earlier observation has to be in state. That
makes it a serial call by construction, which is the distinction drawn above.

## API shape

| Item | Value |
|---|---|
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` |
| Auth | `Bearer <key>`, a per-user `apikey_...`, resolved by `keystore.py` |
| Python SDK | `pip install typesafe-sdk` |
| JS/TS SDK | `npm install @typesafe-ai/sdk` |
| Model | pin `jev-1.13.0`, **not** `jev-latest` |

**Where the key comes from.** `keystore.get_api_key()` checks `TYPESAFE_API_KEY` in the
environment first, then a key stored locally via `route.py --set-api-key`. Every user of
this skill has their own TypeSafe account, so the key is never hardcoded or bundled — see
`SKILL.md`'s "Setting up Jev" section for the exact flow to walk a new user through.

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

client = TypeSafeClient()
response = client.system_one(
    state={"intent": "...", "agents": [...], "machine": {...}},
    questions={
        "agent":       Choice(instructions="...", criteria={...}),
        "blast_radius": Score(instructions="...", criteria=[...]),
        "needs_human": Noul(instructions="..."),
    },
)
response.answers["agent"].choice       # -> "cursor-agent"
response.answers["agent"].confidence   # -> 0.81
response.answers["blast_radius"].score # -> 2.4
response.answers["needs_human"].noul   # -> 0.12
```

`route.py` and `supervise.py` both go through `jev.py`, which uses the official SDK when it
is importable and otherwise posts directly.

**The raw-HTTP contract is verified** against the live endpoint (2026-09-20, `jev-1.13.0`):

- `Authorization: Bearer <key>` — an `x-api-key` header is rejected with 403.
- `model` is **required**; omitting it returns 422.
- Questions encode as `{"type": "choice"|"score"|"noul", "instructions": ..., "criteria": ...}`,
  exactly as the SDK surface suggested.
- Responses carry `usage: {input_tokens, output_tokens}`, which `route.py` records in each
  trace — a full seven-question routing decision runs about 2,160 input tokens, or $0.00009.

One asymmetry worth knowing when reading answers back: a `choice` keys its `probabilities`
by option name, while a `score` keys them by stringified index (`"0"`, `"1"`, …) and ships a
`legend` mapping those indices to the level text.

Run `probe.py --check-jev` to confirm a key against the live API before relying on it.

## Pin the model version

Use `jev-1.13.0`, never the `jev-latest` alias. Thresholds calibrated against one model
version are silently invalidated by an alias shift — the numbers keep working, they just
stop meaning what they meant. TypeSafe also publishes a "model jaggedness" page per
version documenting tasks where the model underperforms; worth reading before finalising a
question set.

## Confidence is not correctness

Two claims that are easy to conflate:

**Calibration is a population property.** Across many decisions, items scored 0.9 are
right more often than items scored 0.7. It does *not* mean any individual 0.9 is correct.

**"Zero hallucinations" is about schema, not semantics.** Jev cannot return a value outside
your declared answer space. It absolutely can be confidently *wrong* about the judgement.
The guarantee is that it will never name an agent you do not have — not that the agent it
names is the right one.

Both are why thresholds must be fitted against recorded traces rather than guessed. See
`calibration.md`.

## What Jev must never be asked to do

| Don't use Jev for | Use instead |
|---|---|
| Exact calculation, counting | Deterministic code |
| Filesystem or database lookup | Code (this is what `probe.py` is) |
| Multi-stage reasoning, planning | A reasoning LLM |
| Writing prose or code | An LLM / the coding agents we route to |
| Anything needing info absent from `state` | Put it in state, or use code |

The state we send is a **summary**: intent, machine profile, repo shape, and the agent
registry. File contents never go in. Jev is a judge, not a reader — and the 32k budget for
state plus the longest question is not the place to spend tokens on source code.
