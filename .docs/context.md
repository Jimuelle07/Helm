# Context: What Jev Is (and What It Isn't)

> Research notes gathered 2026-09-20 from TypeSafe's official docs and launch coverage.
> Everything here is sourced — see [Sources](#sources). Anything we assumed rather than
> verified is marked **[ASSUMPTION]**.

## 1. One-paragraph summary

Jev is a **System One model** from TypeSafe AI, released in early access on **15 September 2026**.
It is *non-generative*: it does not produce tokens, prose, or code. You hand it **state**
(text / JSON) plus a set of **typed questions**, and it returns **typed probabilistic decisions**
in a single non-autoregressive forward pass. The canonical framing:

> **Unstructured or structured state in → typed probabilistic decisions out.**

Or, as a design principle:

> **Code calculates. Jev judges. LLMs reason and generate.**

Mental model from TypeSafe's own docs: *"a five-second expert judgement at machine scale."*

## 2. Why it matters for a harness

A coding-agent harness spends a surprising fraction of its LLM budget on decisions that are not
generation at all: *which model should take this task? is this bash command dangerous? did the
agent actually finish? is this diff worth keeping? should we retry or escalate?*

Today those are answered by an expensive autoregressive model that must be prompted, parsed, and
defended against malformed output. Jev answers them in 70–500 ms, at $0.042 per **million** input
tokens, with a **structurally impossible** chance of returning an off-schema value.

That makes the control plane of an agent harness cheap enough to run **constantly** rather than
occasionally — which is the whole bet of this project.

## 3. The three primitives

All three can be mixed in one request. Questions are evaluated **independently and in parallel**
against the same state, so adding questions barely changes latency. Question B does **not** see
question A's answer.

### Noul — "is this statement true?"

- Returns a single probability `noul` in [0, 1]. `1` = strong yes, `0` = strong no, `0.5` = genuinely uncertain.
- The probability *is* the uncertainty — there is no separate confidence field.
- Use for: detection, policy checks, gating, "did this succeed?".

```python
Noul(instructions="The command would delete files outside the repository working tree")
```

### Choice — "pick one of these"

- Up to **255** options. Returns `choice`, the full `probabilities` distribution, and `confidence`.
- Always include an `other` / `none` escape hatch if the taxonomy might be incomplete.
- Use for: routing, intent, model selection, classification.

```python
Choice(
    instructions="Which agent should handle this task",
    criteria={
        "claude": "Multi-file refactors, architecture, long-horizon reasoning",
        "codex":  "Tight, well-specified single-file edits and test writing",
        "cursor": "Edits that need a repo-wide semantic index",
        "other":  "None of the above fit",
    },
)
```

### Score — "where on this ordered scale?"

- **2–10 ordered levels**. Returns a continuous `score` (can land *between* levels, e.g. `1.035`),
  plus `probabilities` and `confidence`.
- Levels must be **concrete situations**, not vague adjectives. `"no user-visible impact"` beats `"low"`.
- Use for: severity, risk, quality, completeness, relevance.

```python
Score(
    instructions="Blast radius of this change",
    criteria=[
        "Comments or docs only",
        "Single function, covered by tests",
        "Public API or shared module",
        "Migrations, auth, billing, or infrastructure",
    ],
)
```

## 4. API surface

| Item | Value |
|---|---|
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` |
| Model list | `GET /v1/models` |
| Auth | `TYPESAFE_API_KEY` env var (`sk-...`) |
| Python SDK | `pip install typesafe-sdk` (Python 3.10+) |
| JS/TS SDK | `npm install @typesafe-ai/sdk` (Node 20+) |
| Current model | `jev-1.13` — alias `jev-latest` |
| Input types | **Text only.** String, JSON object, or array of text. No image/audio/video. |
| Context | 64k tokens per request; 32k for state + longest question |
| Latency | 70–500 ms end-to-end |
| Cost | $0.042 per million input tokens; **output free** |
| Rate limits | 250k tokens/sec, 1,200 req/min |
| Language | English is primary; other languages supported but less reliable |

### Request / response shape

```jsonc
// request
{
  "state": "string | object | array",
  "questions": { "<key>": "Choice | Score | Noul" }
}

// response
{
  "answers": {
    "<key>": {
      "choice": "string",        // Choice only
      "score": 1.035,            // Score only
      "noul": 0.93,              // Noul only
      "probabilities": {},       // Choice / Score
      "confidence": 0.81         // Choice / Score
    }
  },
  "model": "jev-1.13.0"
}
```

### Python, end to end

```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

client = TypeSafeClient()
response = client.system_one(
    state={"task": "...", "repo": {}, "recent_failures": []},
    questions={
        "agent":       Choice(instructions="...", criteria={}),
        "risk":        Score(instructions="...", criteria=[]),
        "needs_human": Noul(instructions="..."),
    },
)
response.answers["agent"].choice        # -> "claude"
response.answers["agent"].confidence    # -> 0.81
response.answers["risk"].score          # -> 2.4
response.answers["needs_human"].noul    # -> 0.12
```

### TypeScript, end to end

```typescript
import { choice, noul, TypeSafeClient } from "@typesafe-ai/sdk";

const client = new TypeSafeClient();
const response = await client.systemOne({
  state: { task: "...", repo: {} },
  questions: {
    agent: choice("Which agent should handle this", {
      claude: "Multi-file refactors and architecture",
      codex: "Tight single-file edits",
      other: "None of the above",
    }),
    needsHuman: noul("This change requires human approval before execution"),
  },
});
response.answers.agent.choice;
```

## 5. Confidence and calibration — read this before setting a threshold

- Probability and confidence are **first-class outputs**, not diagnostics. Uncertainty is part of
  the normal response.
- **Calibration** means: across a *population*, items scored 0.9 are right more often than items
  scored 0.7. It does **not** mean any individual 0.9 is guaranteed correct.
- "Zero hallucinations" is a claim about **schema**, not **semantics**. Jev cannot return a value
  outside your declared answer space. It absolutely can be confidently *wrong* about the judgement.
- Therefore: **every threshold in this harness must be calibrated against recorded real traces**,
  not guessed. High-risk actions demand a higher bar than low-risk ones.

## 6. Suitability test (from TypeSafe's docs)

Score a candidate decision on these six. 5–6 yes = excellent Jev fit. 3–4 = decompose it.
0–2 = wrong technology.

1. Is AI **deciding** rather than **creating**?
2. Can the answer space be defined beforehand?
3. Can this be one focused judgement?
4. Can all needed information fit in the state?
5. Could a human expert judge this quickly?
6. Will software consume the result directly?

Shortest version: can you phrase it as *"Given this state, tell me X"* where X is a Choice,
a Score, or a probability?

## 7. Hard limits — what Jev must never be asked to do

| Don't use Jev for | Use instead |
|---|---|
| Exact calculation, counting, arithmetic | Deterministic code |
| Database / filesystem lookup | Code |
| Multi-stage reasoning, planning | A reasoning LLM |
| Research requiring external info | LLM + search |
| Writing prose | An LLM |
| **Writing code** | A coding agent (i.e. the things we orchestrate) |
| Anything needing info not in `state` | Put it in state, or use code |

Also: questions must be **genuinely independent**. If question B's answer depends on question A's,
that is a *serial* Jev call, not two parallel ones — and serial calls should represent real
information dependencies, not conversational habit.

## 8. Design rules we will follow

1. **Code calculates. Jev judges. LLMs reason and generate.**
2. **Fan out semantic questions; compose the answers in code.** One request, many questions.
3. Jev calls are **semantic `if` statements** — they replace keyword matching and regex heuristics.
4. **Questions are atomic.** Decompose a big judgement into several narrow ones.
5. **State is explicit and structured.** Pass objects so relationships survive.
6. **Confidence gates automation.** Low confidence → escalate to a reasoning LLM or a human.
7. **All questions and thresholds live in one reviewable place** (TypeSafe's own recommendation),
   so they can be diffed, versioned, and calibrated.
8. **Observe → Judge → Reason → Act.** Jev sits in the judge layer, between raw state and action.

## 9. Known issues

TypeSafe publishes a "model jaggedness" page for Jev 1.13
(`docs.typesafe.ai/model-jaggedness/jev-1.13`) documenting tasks where the model underperforms.
**[TODO]** Read it before finalising the question set, and pin `jev-1.13.0` rather than
`jev-latest` once thresholds are calibrated — an alias shift would silently invalidate them.

## 10. Useful upstream resources

- **Official agent skill** — TypeSafe ships a drop-in skill that teaches a coding agent the Jev API:

  ```bash
  claude plugin marketplace add typesafe-ai/skills
  claude plugin install typesafe@typesafe-ai
  # other agents:
  npx skills add typesafe-ai/skills --skill typesafe-ai
  ```

- **Cookbooks worth mining for this project**: `function_calling`, `llm_guardrails`,
  `skill_suggestion`, `classification_using_confidence`, `confidence-routing`, `parallel_questions`,
  `sde_cascade`, `rerank_typesafe`.
- **LangChain's `langchain_typesafe`** package already ships `TypeSafeClassifier`,
  `ModelRouterMiddleware`, and `AutoModeMiddleware` (a safety gate for `bash`). Even if we don't
  adopt LangChain, these are proof the router + gate patterns work, and worth reading.

## Sources

- [Introduction — TypeSafe AI docs](https://docs.typesafe.ai/introduction)
- [TypeSafe docs index (llms.txt)](https://docs.typesafe.ai/llms.txt)
- [Models — TypeSafe AI docs](https://docs.typesafe.ai/models.md)
- [Agent skill — TypeSafe AI docs](https://docs.typesafe.ai/agent-skill.md)
- [Building a harness with Jev — LangChain blog](https://www.langchain.com/blog/building-a-harness-with-jev)
- [How to Use Jev: a practical guide — DEV](https://dev.to/valyuai/how-to-use-jev-a-practical-guide-to-typesafes-system-one-model-g5e)
- [TypeSafe Jev project reference — GitHub gist](https://gist.github.com/pjburnhill/adf8d28efcad9df037bfdece178ef965)
- [Introducing System One Models & Jev — TypeSafe blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [TypeSafe AI debuts model for machines that plays Doom — The Register](https://www.theregister.com/ai-and-ml/2026/09/16/typesafe-ai-debuts-model-for-machines-that-plays-doom/5296711)
