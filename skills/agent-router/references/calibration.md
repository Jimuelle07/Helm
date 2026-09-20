# Calibration

Every threshold in `scripts/route.py` and `scripts/supervise.py` is currently an
**uncalibrated guess**. This file explains what they do, why they are guesses, and how to
replace them with fitted numbers.

Being explicit about this matters more than it might seem. A threshold that looks like
`0.75` carries an air of having been measured. None of these have been. Until there are
traces to fit against, the honest posture is to prefer *less* automation, not more.

## The thresholds

### Routing thresholds (`route.py`)

| Name | Value | Effect when crossed |
|---|---|---|
| `AUTO_ROUTE_MIN_CONFIDENCE` | 0.75 | Below: recommend only, never auto-execute |
| `CLARIFY_MAX_SPEC_CLARITY` | 1.0 | At or below: ask the user to clarify before routing |
| `HUMAN_GATE_NOUL` | 0.50 | Above: require explicit human approval |
| `AUTO_MAX_BLAST_RADIUS` | 2.0 | Above: require explicit human approval |
| `IRREVERSIBLE_NOUL` | 0.40 | Below: require explicit human approval |
| `FALLBACK_CONFIDENCE_CAP` | 0.50 | Hard ceiling on any non-Jev verdict |

### Completion thresholds (`supervise.py`)

| Name | Value | Effect |
|---|---|---|
| `DONE_MIN_CONFIDENCE` | 0.70 | below: report `uncertain`, ask for a look |
| `SATISFIED_MIN_NOUL` | 0.65 | `task_satisfied` must clear this to call it done |
| `NEEDS_HUMAN_NOUL` | 0.50 | above: escalate |
| `AWAITING_INPUT_NOUL` | 0.60 | above: the run is stuck, not working |
| `MAX_FAILURE_SEVERITY` | 2.0 | above: warn the workspace may be dirty |
| `FALLBACK_CONFIDENCE_CAP` | 0.50 | ceiling on any non-Jev verdict |
| `STUCK_POLLS_BEFORE_KILL` | 3 | consecutive no-progress polls before aborting a `--watch` run |

The asymmetry to preserve when tuning these: **wrongly reporting "done" is much more
expensive than one unnecessary review.** A false "done" means broken work is silently
accepted and discovered later, out of context; a false "review" costs one glance at a log.
Fit these for high recall on the not-actually-done cases.

Note that `FALLBACK_CONFIDENCE_CAP` sits below `DONE_MIN_CONFIDENCE` in *both* files. That
single inequality is what guarantees a degraded judge can never reach the automatic path,
independently of the explicit `source != "jev"` checks. Two mechanisms enforce the same
rule because it is the property least worth losing to a refactor.

Gates are **conjunctive and fail-closed**: every one must pass for `mode: "auto"`. Any
single unmet gate downgrades to `recommend`, and the unmet gates are printed so the user
can see exactly what stopped it.

## Why guesses are acceptable *for now*

The alternative to a guessed threshold is no threshold, which means either no automation
at all or ungated automation. A conservative guess plus honest labelling is strictly
better than both, provided two conditions hold:

1. The numbers are labelled as unfitted everywhere they appear.
2. Traces are recorded from day one, so they *can* be fitted later.

Both hold. `route.py` writes one JSONL row per routing decision to
`~/.cache/agent-router/decisions.jsonl` (override with `AGENT_ROUTER_TRACES`).

`supervise.py` does not yet write traces of its own -- completion verdicts are currently
only observable in its output and in the per-run logs under `AGENT_ROUTER_RUNS`. Wiring the
same trace writer into it is the obvious next step before its thresholds can be fitted.

## What a trace row contains

```jsonc
{
  "ts": "2026-09-20T14:22:05+0800",
  "intent": "add retry logic to the payment client",
  "source": "jev",                    // or "fallback"
  "model": "jev-1.13.0",              // null for fallback verdicts
  "routable": ["claude", "codex", "aider", "..."],
  "answers": { /* all seven raw answers with confidences */ },
  "route": { "mode": "recommend", "agent": "codex", "gates": [...], "signals": {...} },
  "thresholds": { /* the thresholds in force at decision time */ }
}
```

Recording the thresholds *inside each row* means a trace stays interpretable after the
thresholds change. Without it, old rows silently become unanalysable the first time
someone tunes a number.

## How to actually calibrate

Calibration needs an **outcome** column, and a trace row does not have one at write time.
The outcome has to be attached afterwards.

### 1. Collect ground truth

For each recorded decision, record what should have happened:

- Did the routed agent complete the task acceptably?
- If the user overrode the choice, which agent did they pick instead?
- Did an ungated auto-execution cause damage that a gate should have caught?

User overrides are the highest-value signal in the whole system. A disagreement is a
labelled example, and it costs nothing to collect. Aim for at least 50–100 labelled
decisions before fitting anything; below that you are fitting noise.

### 2. Check calibration before tuning thresholds

Bucket the Jev `agent` confidences (0.5–0.6, 0.6–0.7, …) and compute the accuracy within
each bucket. Well-calibrated means the 0.8–0.9 bucket is right roughly 80–90% of the time.

This diagnosis has to come first, because it determines which repair is even appropriate:

- **Calibrated but thresholds misplaced** → move the thresholds.
- **Systematically overconfident** → the question wording or the `competence` lines are
  doing the damage. Fix the cards first; a threshold change only masks it.
- **No relationship between confidence and accuracy** → the question is not answerable
  from the state you are sending. Add the missing information to state, or decompose the
  question.

### 3. Fit each threshold to its own cost asymmetry

There is no single correct value, because the two error types cost different amounts and
the ratio differs per gate.

`AUTO_ROUTE_MIN_CONFIDENCE` trades a wasted run (too low) against nagging the user about
decisions that were fine (too high). Pick the point where the marginal wasted run costs
about as much as the marginal unnecessary prompt.

`AUTO_MAX_BLAST_RADIUS`, `HUMAN_GATE_NOUL`, and `IRREVERSIBLE_NOUL` protect against
damage, and damage is the expensive direction. Fit these for high recall on the bad cases
even at the cost of extra false alarms: a needless approval prompt is cheap, a dropped
table is not.

### 4. Re-fit when anything upstream changes

Thresholds are only valid for the model version, the question wording, and the card text
they were fitted against. Changing a `competence` line changes what Jev sees, which
changes the confidence distribution. That is also why the model is pinned to `jev-1.13.0`
rather than `jev-latest` — an alias shift would invalidate the numbers silently, with
nothing appearing to break.

## Quick inspection

```bash
# How often does each mode fire?
python -c "import json,collections,pathlib; \
p=pathlib.Path.home()/'.cache/agent-router/decisions.jsonl'; \
print(collections.Counter(json.loads(l)['route']['mode'] for l in p.open(encoding='utf-8')))"

# Which agents get chosen, and by which brain?
python -c "import json,collections,pathlib; \
p=pathlib.Path.home()/'.cache/agent-router/decisions.jsonl'; \
print(collections.Counter((json.loads(l)['source'], json.loads(l)['route']['agent']) for l in p.open(encoding='utf-8')))"
```

If `escalate` or `clarify` dominates, the problem is usually upstream of the thresholds:
too few routable agents, or `competence` lines too vague to discriminate between them.

## Privacy

Trace rows contain the user's task text, which may be sensitive. They stay on the local
machine and are never transmitted. Anyone adding trace upload should treat `intent` as
user content requiring explicit consent — and note that nothing about credentials is ever
recorded, by the probe or the router.
