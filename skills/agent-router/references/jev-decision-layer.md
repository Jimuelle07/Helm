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

## API shape

| Item | Value |
|---|---|
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` |
| Auth | `TYPESAFE_API_KEY` (`sk-...`) |
| Python SDK | `pip install typesafe-sdk` |
| JS/TS SDK | `npm install @typesafe-ai/sdk` |
| Model | pin `jev-1.13.0`, **not** `jev-latest` |

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

`route.py` uses the SDK when it is importable, since the SDK owns the wire format, and
otherwise falls back to a raw `urllib` POST. **The raw encoding in `_encode_question()` is
inferred from the documented SDK surface, not verified against a live endpoint.** If that
path returns a 400, check the current schema at `docs.typesafe.ai` and fix that one
function; the SDK path is unaffected, and any failure degrades to the deterministic scorer
rather than breaking the tool.

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
