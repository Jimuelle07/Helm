# Machine Profile (example)

> **This is a fabricated illustration, not a real machine.** It exists so the design docs
> can refer to concrete numbers without checking in anyone's actual hardware or software
> inventory into a repo other people on the team will clone.
>
> Your own copy is personal-machine telemetry — installed CLIs, hardware, local paths — and
> `.gitignore` deliberately excludes it under this project's convention
> (`docs/machine-profile.md`, `docs/machine-profile.local.md`). Regenerate it for yourself
> with `python skills/helm/scripts/probe.py --refresh --repo .`; there is no reason to
> commit it, and every reason not to.

## Hardware

| Field | Value |
|---|---|
| OS | Windows 11 (AMD64) |
| CPU | 12 logical cores |
| RAM | 16 GB total |
| Max VRAM | 8.0 GB |
| Disk free | 60 GB |

- Example discrete GPU — 8.0 GB VRAM (source: `nvidia-smi`)

## Local inference

**Unavailable** — runtime: none installed, models on disk: 0.

With 8 GB of VRAM the practical ceiling would be roughly a 10–13B four-bit model even if a
runtime were installed.

## Installed agents

| Agent | Routable | Context class | Caveats |
|---|---|---|---|
| `claude` | yes | xlarge | - |
| `codex` | yes | large | - |
| `aider` | yes | medium | creds not detected |
| `gemini` | yes | xlarge | - |

**Not installed:** everything else in `scripts/cards/` — the probe only reports what
`shutil.which()` actually resolves on the machine it runs on.

## Decision layer

**Jev is live and verified on this machine.** A key is configured (stored per-user via
`route.py --set-api-key`, not in the environment).

Verify at any time with:

```
python skills/helm/scripts/probe.py --check-jev
```

## Toolchain

Confirmed available: `node`, `npm`, `python`, `git` — whatever `probe.py` resolves on your
PATH. See `references/capability-cards.md` for how a card turns "resolves on PATH" into a
routable agent.
