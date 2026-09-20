# Idea: An Orchestrator That Already Knows Its Tools

> Companion to [`context.md`](./context.md) (what Jev is) and [`system-design.md`](./system-design.md)
> (how we build it). This file is the *thesis*: the problem, the bet, and why Jev is the right
> shape for the decision layer.

## 1. The problem, stated precisely

> **An LLM does not know its own tool unless it gets called.**

This is the sentence the whole project hangs on, so it is worth unpacking into the three distinct
failures it actually names.

**(a) Discovery is lazy and lossy.** A coding agent learns what it can do by reading a tool list
injected into its prompt. Anything not in that list does not exist. The six other agent CLIs sitting
on the same machine — each with its own model, price, context window, and speciality — are invisible.
Claude Code cannot route work to Codex. Codex cannot hand a repo-wide semantic search to
`cursor-agent`. They are siblings who have never been introduced.

**(b) Capability is discovered by *failure*, not by *knowledge*.** When an agent does try something
outside its competence, it finds out by burning tokens and wall-clock: it attempts, fails, retries,
apologises, retries differently. The knowledge "this task needed a different tool" is produced
*after* paying for the attempt, and then thrown away when the session ends.

**(c) The meta-decision is made by the wrong machine.** "Which model should do this?" is a
*judgement*, not a *generation*. Today it is answered — if at all — by an autoregressive model that
must be prompted, must emit tokens, must be parsed, and may emit something off-schema. We pay
generation prices for a classification.

The compounding effect: every agent is a local optimum that cannot see the others, and the cost of
finding that out is paid per-attempt, forever.

## 2. What is actually on this machine

The argument above is abstract until you run the probe. On the development box this was
written on (shape of the report illustrated in
[`machine-profile.example.md`](./machine-profile.example.md) — a fabricated machine, not a
real one; your own real survey is local-only and gitignored, see that file's header):

| Agent CLI | Version | Notes |
|---|---|---|
| `claude` | 2.1.278 | Claude Code |
| `codex` | 0.153.4 | ChatGPT-subscription auth, not per-token API |
| `cursor-agent` | 2026.09.10 | **Missed by a naive POSIX probe** — see §4 |
| `gemini` | 0.58.0 | |
| `aider` | 0.86.2 | |
| `opencode` | 1.18.31 | |
| `copilot` | 1.0.83 | GitHub Copilot CLI |

Seven headless-capable coding agents, fronting four model families, installed side by side — and
**not one of them can name the other six.** That is the gap. It is not hypothetical; it is sitting
in `PATH` right now.

Meanwhile the hardware says something equally concrete: 6 GB of VRAM, 15.7 GB of RAM (3.7 GB free at
probe time), and **no Ollama binary and zero local models on disk**. So for this machine, today,
"route it to a cheap local model" is not an available move — and an orchestrator that recommends one
is hallucinating capability. Machine truth has to constrain the answer space.

## 3. The bet

Three claims, in order:

**Claim 1 — Tool knowledge should be *declared*, not *discovered*.**
A coding agent's competence profile is stable across sessions. `aider` is good at surgical
single-file edits under git; `claude` is good at long-horizon multi-file reasoning. That does not
change between Tuesday and Wednesday. It is *reference data*, and reference data belongs in a
registry that is probed once and cached — not rediscovered by trial-and-error inside every session.

**Claim 2 — Selection is a judgement, and judgement is Jev's job.**
"Given this task and these seven available agents, which one?" is exactly the shape
`context.md` §6 calls an excellent fit: AI is *deciding* not *creating*, the answer space is
knowable beforehand, it is one focused judgement, the state fits, a human expert would answer in
five seconds, and software consumes the result directly. Six out of six.

**Claim 3 — Availability should be enforced *structurally*, not by filtering afterwards.**
This is the keystone, and it is the reason Jev specifically — rather than a cheap LLM — is the right
component. Jev's `Choice` returns a value from a declared answer space and **cannot return anything
outside it** (`context.md` §5: "zero hallucinations" is a claim about *schema*). So we build the
`criteria` dict **dynamically, from the agents the probe actually found**:

```python
Choice(
    instructions="Which installed agent should execute this task",
    criteria={name: card.one_line_competence for name, card in registry.available().items()},
)
```

An agent that is not installed is not a key in that dict, and therefore **cannot be recommended —
not unlikely, impossible.** "Best model available *on the user's machine*" stops being a prompt
instruction we hope is respected and becomes a property of the type system. That is the whole
reason to reach for a System One model here instead of a small LLM: we are buying a *guarantee*,
not a *tendency*.

## 4. What the probe taught us (and why it belongs in the design)

Building the probe surfaced failures that a hand-written registry would have shipped as bugs. They
are recorded here because each one is a permanent requirement, not a one-off fix:

- **`command -v` under-reports on Windows.** `cursor-agent` installs as `cursor-agent.cmd`. Git Bash
  does not apply `PATHEXT`, so a POSIX probe reports it missing while the tool is installed and
  working. A probe that under-reports silently shrinks the answer space and the orchestrator
  confidently routes around a perfectly good agent. → *Probe must be PATHEXT-aware.*
- **Shell builtins are false positives.** `command -v continue` succeeds because `continue` is a
  bash builtin. → *Probe must filter by command type, not just resolution success.*
- **WMI lies about VRAM.** `Win32_VideoController.AdapterRAM` is a 32-bit field; it reported 4 GB
  for a card that `nvidia-smi` correctly reports as 6141 MiB. → *Prefer vendor tools; treat WMI as
  a fallback.*
- **A `PATH` entry is not an installation.** `...\Programs\Ollama` is on `PATH` and does not exist;
  `~/.ollama/models` exists and is empty. → *Resolve and verify; never infer capability from `PATH`.*
- **Auth mode is a capability.** `codex` authenticates as `chatgpt` (subscription), not via
  `OPENAI_API_KEY`. Its marginal cost curve is nothing like a per-token API agent's, and routing
  that ignores this optimises the wrong quantity. → *Auth mode belongs in the registry.*
- **Windows Python defaults to cp1252.** Reading `~/.codex/models_cache.json` without
  `encoding="utf-8"` raises `UnicodeDecodeError`. → *Every file read is explicitly UTF-8.*

The theme: **the registry is only as good as the probe, and the probe is where the platform-specific
truth lives.** Judgement quality is downstream of observation quality.

## 5. The shape of the answer

```
                 ┌──────────── OBSERVE (code, no model) ─────────────┐
   user intent → │ machine profile + PATHEXT-aware agent probe       │
                 │        → Capability Registry (cached)             │
                 └───────────────────────┬───────────────────────────┘
                                         │  state
                 ┌───────────────────────▼───────────────────────────┐
                 │ JUDGE (Jev, one request, questions in parallel)   │
                 │  task_kind · agent · blast_radius · spec_clarity  │
                 │  context_breadth · needs_human · reversible       │
                 └───────────────────────┬───────────────────────────┘
                                         │  typed answers + confidence
                 ┌───────────────────────▼───────────────────────────┐
                 │ COMPOSE (code) — thresholds, gates, fallback      │
                 └───────────────────────┬───────────────────────────┘
                                         │
                 ┌───────────────────────▼───────────────────────────┐
                 │ ACT — invoke the chosen agent CLI headless        │
                 └───────────────────────────────────────────────────┘
```

Restating `context.md`'s design rule #1 in this project's terms:

> **The probe observes. Jev judges. The chosen agent generates. Code composes and executes.**

Each layer does the one thing it is cheapest and safest at. Jev never writes code; the coding agents
never pick themselves; the probe never guesses.

This ships as a **distributable agent skill**, not a local script, because the problem it solves is
not specific to one machine — every developer with more than one agent CLI installed has exactly
this gap. That has a sharp design consequence: the probe cannot assume *anything* about the host.
Not the OS, not the shell, not which agents exist, not that an API key is present. Everything it
reports is measured on the machine it is running on, and everything it cannot measure it reports as
unknown rather than guessing a default. A skill that hardcodes the author's laptop is worse than no
skill, because it is confidently wrong on every machine but one.

## 6. Why this is worth building rather than prompting

A reasonable objection: *why not just put the agent list in a system prompt and let a cheap LLM
pick?* Four reasons, in descending order of importance:

1. **Schema guarantees.** The prompt approach can name an agent that is not installed. The Jev
   approach cannot. For a component whose entire job is "stay inside what this machine has," that
   distinction is the product.
2. **Calibrated confidence is a first-class output.** `confidence` and the full `probabilities`
   distribution come back on every `Choice`. That is what lets us say *"route automatically above
   0.75, ask a human below"* — and then tune that number against recorded traces
   (`context.md` §5). An LLM's self-reported confidence is not calibrated and cannot be tuned this way.
3. **Cost and latency.** 70–500 ms and $0.042 per *million* input tokens, output free. Cheap enough
   to run the control plane on **every** task rather than only on the ones that look hard — which
   is precisely the condition under which routing starts paying for itself.
4. **One reviewable surface.** Every question and every threshold lives in a single versioned file
   (`judge/questions.py`), so the system's judgement can be diffed, reviewed, and calibrated as a
   unit — `context.md` design rule #7.

## 7. Non-goals

- **Not a model.** We are not training anything. "Orchestrator model" here means the orchestration
  *layer*; Jev supplies the learned judgement.
- **Not a replacement for the agents.** We route to Claude Code, Codex, and friends — we do not
  reimplement them.
- **Not an autonomous committer.** The default posture is recommend-and-explain. Execution is
  opt-in and gated by a `needs_human` Noul plus a blast-radius Score.
- **Not cloud-dependent for correctness.** No `TYPESAFE_API_KEY` is set on this machine today, so a
  deterministic fallback scorer is a first-class path, not an afterthought. The harness must be
  useful before the key exists — and must say clearly which brain answered.

## 8. How we will know it worked

| Question | Success looks like |
|---|---|
| Does it know its tools without calling them? | `doctor` lists all 7 agents + hardware with zero model invocations |
| Does it stay inside the machine? | No recommendation names an uninstalled agent, *by construction* |
| Is the judgement good? | Routing matches human expert choice on a recorded trace set |
| Is it honest about uncertainty? | Low-confidence routes escalate rather than guess |
| Does it degrade gracefully? | Full function with no API key, and says so |
