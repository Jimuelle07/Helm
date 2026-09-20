#!/usr/bin/env python3
"""Discover what this machine can actually do, without invoking a single model.

Emits a JSON capability registry: hardware, installed coding-agent CLIs, their
declared competences, and their auth readiness. This is the "observe" layer --
it answers "what tools exist here?" so that no agent has to discover its
siblings by trial and error.

Usage:
    python probe.py                 # human-readable summary
    python probe.py --json          # machine-readable registry (feed to route.py)
    python probe.py --json --refresh  # bypass the cache

Design notes worth keeping in mind if you edit this:

  * shutil.which is the portable primitive. On Windows it applies PATHEXT, so it
    finds `cursor-agent.CMD`; a POSIX `command -v cursor-agent` does not and
    reports the tool missing. It also only matches real files, so shell builtins
    (`command -v continue` succeeds in bash!) never produce false positives.
  * A PATH entry is not an installation. Resolve, then trust only the resolution.
  * Never read secret values. Auth detection checks for the *presence* of env
    vars and config files, never their contents.
  * Every file read is explicitly UTF-8: Windows Python defaults to cp1252 and
    will raise UnicodeDecodeError on perfectly normal agent config files.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import keystore  # noqa: E402

CARDS_DIR = Path(__file__).parent / "cards"
CACHE_PATH = Path(
    os.environ.get("HELM_CACHE")
    or Path.home() / ".cache" / "helm" / "registry.json"
)
CACHE_TTL_SECONDS = 24 * 3600
VERSION_TIMEOUT = 20
IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def read_text(path: Path) -> str | None:
    """UTF-8 with replacement. Windows Python defaults to cp1252 and blows up."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def run(argv: list[str], timeout: int = 10) -> tuple[int, str]:
    """Run a command, never through a shell. Returns (rc, combined output)."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            shell=False,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, f"{type(exc).__name__}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def expand(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


# CLI tools colour their output even when piped. Those escape codes break
# pattern matching here and waste judge tokens downstream, so strip them once
# at the boundary rather than defending against them everywhere.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


# --------------------------------------------------------------------------- #
# hardware
# --------------------------------------------------------------------------- #

def probe_memory() -> dict:
    """Total and available RAM in GB, cross-platform, no third-party deps."""
    try:
        if IS_WINDOWS:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return {
                "total_gb": round(stat.ullTotalPhys / 1024**3, 1),
                "available_gb": round(stat.ullAvailPhys / 1024**3, 1),
            }

        if sys.platform == "darwin":
            rc, out = run(["sysctl", "-n", "hw.memsize"])
            total = int(out) / 1024**3 if rc == 0 and out.isdigit() else None
            return {"total_gb": round(total, 1) if total else None, "available_gb": None}

        meminfo = read_text(Path("/proc/meminfo")) or ""
        fields = {}
        for line in meminfo.splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                fields[key] = int(parts[0]) / 1024**2  # kB -> GB
        return {
            "total_gb": round(fields["MemTotal"], 1) if "MemTotal" in fields else None,
            "available_gb": round(fields["MemAvailable"], 1) if "MemAvailable" in fields else None,
        }
    except Exception:
        return {"total_gb": None, "available_gb": None}


def probe_gpus() -> list[dict]:
    """GPU name + VRAM.

    nvidia-smi is authoritative. Windows' Win32_VideoController.AdapterRAM is a
    32-bit field and under-reports large cards (observed: 4 GB reported for a
    6141 MiB card), so it is only ever used to *name* a GPU, never to size it --
    a wrong number is worse than a null, because routing would silently believe it.
    """
    gpus: list[dict] = []
    if shutil.which("nvidia-smi"):
        rc, out = run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"]
        )
        if rc == 0:
            for line in out.splitlines():
                name, _, mib = line.partition(",")
                mib = mib.strip()
                if name.strip():
                    gpus.append({
                        "name": name.strip(),
                        "vram_gb": round(int(mib) / 1024, 1) if mib.isdigit() else None,
                        "source": "nvidia-smi",
                        "vendor": "nvidia",
                    })
    if not gpus and IS_WINDOWS:
        rc, out = run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "ForEach-Object { $_.Name }"],
            timeout=20,
        )
        if rc == 0:
            for line in out.splitlines():
                if line.strip():
                    gpus.append({
                        "name": line.strip(),
                        "vram_gb": None,  # deliberately null: WMI's value is unreliable
                        "source": "wmi-name-only",
                        "vendor": None,
                    })
    return gpus


def probe_cpu_name() -> str | None:
    if IS_WINDOWS:
        return os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or None
    if sys.platform == "darwin":
        rc, out = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        return out if rc == 0 and out else None
    for line in (read_text(Path("/proc/cpuinfo")) or "").splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor() or None


def probe_hardware() -> dict:
    mem = probe_memory()
    gpus = probe_gpus()
    try:
        usage = shutil.disk_usage(Path.home())
        disk_free = round(usage.free / 1024**3, 1)
    except OSError:
        disk_free = None
    vrams = [g["vram_gb"] for g in gpus if g.get("vram_gb")]
    return {
        "os": f"{platform.system()} {platform.release()}",
        "platform": sys.platform,
        "arch": platform.machine(),
        "cpu": probe_cpu_name(),
        "cpu_logical_cores": os.cpu_count(),
        "ram_total_gb": mem["total_gb"],
        "ram_available_gb": mem["available_gb"],
        "gpus": gpus,
        "max_vram_gb": max(vrams) if vrams else None,
        "disk_free_gb": disk_free,
    }


# --------------------------------------------------------------------------- #
# local inference
# --------------------------------------------------------------------------- #

def probe_local_inference(hardware: dict) -> dict:
    """Local inference needs a runtime AND downloaded weights. Either alone is nothing.

    ~/.ollama/models existing but empty is the common trap: the directory
    survives an uninstall, so presence of the folder proves nothing.
    """
    ollama_bin = shutil.which("ollama")
    models: list[str] = []
    manifests = Path.home() / ".ollama" / "models" / "manifests"
    if manifests.is_dir():
        models = [
            "/".join(p.relative_to(manifests).parts[1:])
            for p in manifests.rglob("*") if p.is_file()
        ]
    if ollama_bin and not models:
        rc, out = run([ollama_bin, "list"], timeout=15)
        if rc == 0:
            models = [
                ln.split()[0] for ln in out.splitlines()[1:]
                if ln.strip() and not ln.startswith("NAME")
            ]
    vram = hardware.get("max_vram_gb")
    return {
        "runtime": "ollama" if ollama_bin else None,
        "runtime_path": ollama_bin,
        "models": sorted(set(models)),
        "available": bool(ollama_bin and models),
        "vram_gb": vram,
        # Rough practical ceiling for a 4-bit quantised model, leaving room for
        # context and the desktop compositor. Advisory only -- it gates nothing.
        "largest_practical_model_b": (
            int(vram * 1.8) if isinstance(vram, (int, float)) and vram else None
        ),
    }


# --------------------------------------------------------------------------- #
# agents
# --------------------------------------------------------------------------- #

def load_cards() -> list[dict]:
    cards = []
    for path in sorted(CARDS_DIR.glob("*.json")):
        raw = read_text(path)
        if not raw:
            continue
        try:
            cards.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            print(f"warn: skipping malformed card {path.name}: {exc}", file=sys.stderr)
    return cards


def probe_auth(card: dict) -> dict:
    """Auth *readiness*, never auth *material*. We read no secret values."""
    auth = card.get("auth") or {}
    env_present = [v for v in auth.get("env", []) if os.environ.get(v)]
    cfg_present = [c for c in auth.get("config", []) if expand(c).exists()]
    return {
        "modes": auth.get("modes", []),
        "env_vars_set": env_present,
        "config_present": cfg_present,
        # Heuristic only: "we spotted something that looks like a credential".
        # Never sufficient to disqualify -- see the faultline note by routable().
        "ready": bool(env_present or cfg_present) or auth.get("modes") == ["none"],
        # Filled in by verify_all_auth() when a real check runs.
        "state": "unknown",
        "detail": "not verified",
        "verified": False,
        "checked_at": None,
    }


def probe_agent(card: dict, want_version: bool) -> dict:
    resolved, matched_name = None, None
    for name in card.get("bin_names", [card["name"]]):
        found = shutil.which(name)
        if found and Path(found).is_file():
            resolved, matched_name = found, name
            break

    entry = {
        "name": card["name"],
        "display_name": card.get("display_name", card["name"]),
        "installed": resolved is not None,
        "path": resolved,
        "bin": matched_name,
        "version": None,
        "status": "absent",
        "competence": card.get("competence", ""),
        "strengths": card.get("strengths", []),
        "weaknesses": card.get("weaknesses", []),
        "task_fit": card.get("task_fit", {}),
        "context_class": card.get("context_class", "medium"),
        "headless": card.get("headless"),
        "contract_verified": card.get("contract_verified", False),
        "requires": card.get("requires", {}),
        "auth": probe_auth(card),
    }
    if not resolved:
        return entry

    entry["status"] = "installed"
    if want_version:
        rc, out = run([resolved, "--version"], timeout=VERSION_TIMEOUT)
        if rc == 124:
            # Responded too slowly to characterise, but it is on disk. "degraded"
            # keeps it visible without asserting a version we never saw.
            entry["status"] = "degraded"
        elif rc == 0 and out:
            entry["version"] = out.splitlines()[0].strip()[:80]
    return entry


# --------------------------------------------------------------------------- #
# auth verification -- the faultline
#
# Installing a CLI and authenticating it are separate acts, and users routinely
# do the first without the second. An unauthenticated agent looks perfectly
# healthy to a `--version` probe and then fails the moment it is asked to call
# a model, which wastes a whole dispatch cycle and surfaces as a confusing
# runtime error rather than a clear precondition failure.
#
# The faultline is the distinction between two things that are easy to
# conflate:
#
#   absence of evidence   -- we could not find a credential. Means nothing:
#                            tokens live in OS keychains and browser sessions
#                            we cannot enumerate. NEVER disqualifies.
#   evidence of absence   -- the tool's own status command said it is not
#                            logged in. That is authoritative. DOES disqualify.
#
# Only a card's `auth_check` can produce the second. Everything else produces
# "unknown", which is reported as a caveat and left routable. On this machine
# the heuristic called cursor-agent "no credentials detected" while
# `cursor-agent status` reported a live session -- a false negative that would
# have dropped a working agent had the heuristic been allowed to gate.
# --------------------------------------------------------------------------- #

# A diagnosis is only useful with the remedy attached. Keyed by card name; an
# agent without an entry simply gets no hint rather than a guessed one.
LOGIN_HINTS = {
    "claude": "claude auth login",
    "codex": "codex login",
    "cursor-agent": "cursor-agent login",
    "opencode": "opencode auth login",
    "copilot": "copilot login",
    "gemini": "run `gemini` once and pick an auth method, or set GEMINI_API_KEY",
    "aider": "set OPENAI_API_KEY or ANTHROPIC_API_KEY in your environment",
    "goose": "goose configure",
    "qwen": "run `qwen` once to authenticate, or set DASHSCOPE_API_KEY",
    "amp": "amp login",
    "droid": "droid login",
    "ollama": "ollama pull <model> to download at least one model",
}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _redact(text: str) -> str:
    """Auth status output often names the logged-in account. The registry gets
    written to disk and pasted into issues, so strip addresses out of it."""
    return EMAIL_RE.sub("<redacted>", text)


def verify_auth(card: dict, resolved: str) -> dict:
    """Ask the tool itself whether it is authenticated.

    Returns state in {"authenticated", "unauthenticated", "unknown"} plus a
    short human-readable detail. Anything ambiguous resolves to "unknown" --
    a check that cannot answer must not be allowed to condemn a working agent.
    """
    check = card.get("auth_check")
    if not check:
        return {"state": "unknown", "detail": "no auth status command for this CLI",
                "verified": False}

    argv = list(check.get("argv") or [])
    if not argv:
        return {"state": "unknown", "detail": "malformed auth_check", "verified": False}
    argv[0] = resolved  # always invoke the path we resolved, not a bare name

    rc, out = run(argv, timeout=int(check.get("timeout", 30)))
    if rc == 124:
        return {"state": "unknown", "detail": "auth check timed out", "verified": False}

    blob = strip_ansi(out)
    fail_pat, ok_pat = check.get("fail_pattern"), check.get("ok_pattern")

    # fail_pattern is tested first: a tool can exit 0 while printing "not
    # logged in", and the explicit negative is more trustworthy than rc.
    if fail_pat and re.search(fail_pat, blob, re.I):
        return {"state": "unauthenticated", "detail": _first_line(blob) or "reported not logged in",
                "verified": True}
    if ok_pat and re.search(ok_pat, blob, re.I):
        return {"state": "authenticated", "detail": _first_line(blob), "verified": True}
    if ok_pat:
        # An ok_pattern was declared and did not match. Treat a clean exit as
        # ambiguous rather than authenticated -- the output shape may simply
        # have changed in a new CLI version, and guessing "fine" here is how
        # you route to a dead agent.
        return {"state": "unknown",
                "detail": "auth status output not recognised (CLI may have changed)",
                "verified": False}
    if rc == 0:
        return {"state": "authenticated", "detail": _first_line(blob), "verified": True}
    return {"state": "unauthenticated", "detail": _first_line(blob) or f"exit code {rc}",
            "verified": True}


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = _redact(line.strip())
        if line:
            return line[:100]
    return ""


def verify_all_auth(agents: list[dict], cards_by_name: dict, only: set[str] | None = None) -> None:
    """Run every auth check in parallel and merge the results in place.

    Parallel because these are independent subprocess round-trips: seven checks
    run in about as long as the slowest one instead of the sum. `only` restricts
    the sweep to named agents, which is what makes the re-check-after-login path
    cheap (see load_cached).
    """
    targets = [
        a for a in agents
        if a["installed"] and a.get("path")
        and (only is None or a["name"] in only)
        and cards_by_name.get(a["name"], {}).get("auth_check")
    ]
    if not targets:
        return

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        futures = {
            pool.submit(verify_auth, cards_by_name[a["name"]], a["path"]): a
            for a in targets
        }
        for fut in futures:
            agent = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:  # a broken check must never sink the probe
                result = {"state": "unknown", "detail": f"check errored: {type(exc).__name__}",
                          "verified": False}
            agent["auth"].update(result)
            agent["auth"]["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")


def routable(agent: dict, local: dict) -> tuple[bool, str]:
    """Can this agent actually be handed a task right now?

    Installed is not the same as routable: an IDE launcher has no headless
    contract, Ollama with no weights cannot run, and a CLI that has never been
    logged in will fail on its first model call. Filtering here is what keeps
    the routing answer space honest.

    The auth rule is asymmetric on purpose -- see the faultline note above.
    Only a definitive "unauthenticated" from the tool's own status command
    disqualifies; an undetected credential never does.
    """
    if not agent["installed"]:
        return False, "not installed"
    if agent["status"] == "degraded":
        return False, "did not respond to --version"
    if not agent.get("headless"):
        return False, "no headless invocation contract"
    if agent.get("auth", {}).get("state") == "unauthenticated":
        detail = agent["auth"].get("detail") or "not logged in"
        return False, f"installed but not authenticated ({detail})"
    req = agent.get("requires") or {}
    if req.get("local_inference") and not local["available"]:
        return False, "requires local inference (no runtime or no models)"
    min_vram = req.get("min_vram_gb")
    if min_vram and (local.get("vram_gb") or 0) < min_vram:
        return False, f"requires {min_vram} GB VRAM, found {local.get('vram_gb') or 0}"
    return True, "ok"


# --------------------------------------------------------------------------- #
# repo context
# --------------------------------------------------------------------------- #

EXT_LANG = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".php": "php", ".cs": "csharp", ".cpp": "cpp", ".c": "c", ".swift": "swift",
    ".kt": "kotlin", ".sh": "shell", ".sql": "sql",
}
SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    "target", ".next", ".tox", "vendor", ".mypy_cache", ".pytest_cache",
}


def probe_repo(root: Path) -> dict:
    info: dict = {"root": str(root), "vcs": None, "branch": None, "dirty": None,
                  "languages": [], "file_count": 0, "has_tests": False}
    if shutil.which("git"):
        rc, out = run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"])
        if rc == 0:
            info["vcs"] = "git"
            info["branch"] = out.strip()
            rc2, out2 = run(["git", "-C", str(root), "status", "--porcelain"])
            if rc2 == 0:
                info["dirty"] = bool(out2.strip())

    counts: dict[str, int] = {}
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            total += 1
            lang = EXT_LANG.get(Path(fn).suffix.lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
            low = fn.lower()
            if low.startswith("test_") or "_test." in low or low.startswith("spec."):
                info["has_tests"] = True
        if "tests" in [d.lower() for d in dirnames] or "test" in [d.lower() for d in dirnames]:
            info["has_tests"] = True
        if total > 20000:  # a summary, not a census
            break
    info["file_count"] = total
    info["languages"] = [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:5]]
    return info


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

def build_registry(repo_root: Path | None = None, want_version: bool = True,
                   want_auth: bool = True) -> dict:
    hardware = probe_hardware()
    local = probe_local_inference(hardware)
    cards = load_cards()
    agents = [probe_agent(c, want_version) for c in cards]
    if want_auth:
        verify_all_auth(agents, {c["name"]: c for c in cards})
    for a in agents:
        ok, why = routable(a, local)
        a["routable"] = ok
        a["routable_reason"] = why
    return {
        "schema": "helm/registry/1",
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hardware": hardware,
        "local_inference": local,
        "agents": agents,
        "repo": probe_repo(repo_root) if repo_root else None,
        "jev": probe_jev(),
    }


def probe_jev() -> dict:
    """Never cached: a key set with --set-api-key must take effect immediately,
    not after the 24h registry cache expires. Cheap to recompute either way."""
    key = keystore.get_api_key()
    return {
        "api_key_present": bool(key),
        "api_key_source": keystore.key_source(),  # "env" | "stored" | None
        "sdk_installed": _sdk_installed(),
    }


def _sdk_installed() -> bool:
    import importlib.util
    return importlib.util.find_spec("typesafe_sdk") is not None


def _recheck_failed_auth(reg: dict) -> None:
    """Re-verify only the agents the cache says are logged out.

    Without this, telling someone "codex is installed but not authenticated"
    would be followed by them logging in and the tool still insisting it is
    broken until the 24h cache expired -- the worst possible moment to be
    stale, since it is the exact loop we are asking them to close. Agents that
    verified fine stay cached, so the common path costs nothing and only the
    already-broken ones pay for a re-check.
    """
    failed = {a["name"] for a in reg.get("agents", [])
              if a.get("auth", {}).get("state") == "unauthenticated"}
    if not failed:
        return
    cards = {c["name"]: c for c in load_cards()}
    verify_all_auth(reg["agents"], cards, only=failed)
    local = reg.get("local_inference", {"available": False, "vram_gb": None})
    for a in reg["agents"]:
        ok, why = routable(a, local)
        a["routable"] = ok
        a["routable_reason"] = why


def load_cached(refresh: bool, repo_root: Path | None, want_version: bool,
                want_auth: bool = True) -> dict:
    if not refresh and CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            raw = read_text(CACHE_PATH)
            if raw:
                try:
                    reg = json.loads(raw)
                    reg["from_cache"] = True
                    reg["cache_age_seconds"] = int(age)
                    # Free RAM is a point-in-time reading, never a cached capability.
                    reg["hardware"]["ram_available_gb"] = probe_memory()["available_gb"]
                    # Same reasoning for the API key: --set-api-key must take effect
                    # on the very next call, not after the cache expires.
                    reg["jev"] = probe_jev()
                    if want_auth:
                        _recheck_failed_auth(reg)
                    if repo_root:
                        reg["repo"] = probe_repo(repo_root)
                    return reg
                except (json.JSONDecodeError, KeyError):
                    pass
    reg = build_registry(repo_root, want_version, want_auth)
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(reg, indent=2), encoding="utf-8")
    except OSError:
        pass
    reg["from_cache"] = False
    return reg


# --------------------------------------------------------------------------- #
# presentation
# --------------------------------------------------------------------------- #

def summarize(reg: dict) -> str:
    hw, local = reg["hardware"], reg["local_inference"]
    lines = ["MACHINE", f"  {hw['os']} ({hw['arch']})  |  {hw['cpu'] or 'unknown CPU'}"]
    lines.append(
        f"  {hw['cpu_logical_cores']} logical cores  |  "
        f"RAM {hw['ram_total_gb']} GB total, {hw['ram_available_gb']} GB free  |  "
        f"disk {hw['disk_free_gb']} GB free"
    )
    for g in hw["gpus"]:
        vram = f"{g['vram_gb']} GB" if g["vram_gb"] else "VRAM unknown"
        lines.append(f"  GPU: {g['name']} ({vram}, via {g['source']})")

    if local["available"]:
        lines.append(
            f"  Local inference: {local['runtime']} with {len(local['models'])} model(s); "
            f"~{local['largest_practical_model_b']}B practical ceiling"
        )
    else:
        why = "no runtime installed" if not local["runtime"] else "runtime present but no models"
        lines.append(f"  Local inference: unavailable ({why})")

    rt = [a for a in reg["agents"] if a["routable"]]
    inst_only = [a for a in reg["agents"] if a["installed"] and not a["routable"]]

    lines.append("")
    lines.append(f"ROUTABLE AGENTS ({len(rt)})")
    for a in sorted(rt, key=lambda x: x["name"]):
        ver = a["version"] or "version unknown"
        auth = a.get("auth", {})
        flags = []
        if not a["contract_verified"]:
            flags.append("contract unverified")
        if auth.get("state") == "authenticated":
            flags.append("auth verified")
        elif not auth.get("verified"):
            flags.append("auth unverified")
        suffix = ("  [" + "; ".join(flags) + "]") if flags else ""
        lines.append(f"  {a['name']:<14} {ver:<26}{suffix}")
        lines.append(f"  {'':<14} {a['competence']}")

    # Broken-auth agents get their own block rather than being buried in a
    # generic "not routable" list: this is the failure users actually hit, and
    # it is the one with a one-command fix.
    unauth = [a for a in inst_only if a.get("auth", {}).get("state") == "unauthenticated"]
    other = [a for a in inst_only if a not in unauth]

    if unauth:
        lines.append("")
        lines.append(f"INSTALLED BUT NOT AUTHENTICATED ({len(unauth)})  <- log in to use these")
        for a in sorted(unauth, key=lambda x: x["name"]):
            lines.append(f"  {a['name']:<14} {a['auth'].get('detail', 'not logged in')}")
            hint = LOGIN_HINTS.get(a["name"])
            if hint:
                lines.append(f"  {'':<14} fix: {hint}")

    if other:
        lines.append("")
        lines.append(f"INSTALLED BUT NOT ROUTABLE ({len(other)})")
        for a in sorted(other, key=lambda x: x["name"]):
            lines.append(f"  {a['name']:<14} {a['routable_reason']}")

    missing = [a["name"] for a in reg["agents"] if not a["installed"]]
    if missing:
        lines.append("")
        lines.append(f"NOT INSTALLED ({len(missing)}): {', '.join(sorted(missing))}")

    jev = reg["jev"]
    lines.append("")
    lines.append("DECISION LAYER")
    if jev["api_key_present"]:
        src = "TYPESAFE_API_KEY env var" if jev["api_key_source"] == "env" else "locally stored key"
        lines.append(f"  Jev: API key found ({src}) -- judgement available")
    else:
        lines.append("  Jev: no API key configured -- route.py will use the deterministic")
        lines.append("       fallback scorer and cap its confidence at 0.5 (recommend-only)")
        lines.append("       Set one with:  python probe.py --set-api-key apikey_...")

    if reg.get("repo"):
        r = reg["repo"]
        langs = ", ".join(r["languages"]) or "unknown"
        lines.append("")
        lines.append("REPO")
        lines.append(
            f"  {r['root']}  |  {r['vcs'] or 'no vcs'}"
            + (f"@{r['branch']}" if r['branch'] else "")
            + (" (dirty)" if r["dirty"] else "")
        )
        lines.append(f"  {r['file_count']} files  |  {langs}  |  tests: {r['has_tests']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Probe machine + installed coding agents.",
        # Exact flags only. argparse abbreviates by default, which made the
        # destructive `--clear-api-key` reachable as `--clear` -- a typo away
        # from silently deleting the user's credentials.
        allow_abbrev=False)
    ap.add_argument("--json", action="store_true", help="emit the registry as JSON")
    ap.add_argument("--refresh", action="store_true", help="bypass the cache")
    ap.add_argument("--repo", metavar="PATH", help="also summarise this repository")
    ap.add_argument("--no-version", action="store_true",
                    help="skip --version probes (much faster)")
    ap.add_argument("--no-verify-auth", action="store_true",
                    help="skip asking each CLI whether it is logged in (faster, "
                         "but unauthenticated agents will not be filtered out)")
    ap.add_argument("--check-jev", action="store_true",
                    help="make one live Jev request to confirm the API key works, then exit")
    keystore.add_key_args(ap)
    args = ap.parse_args()

    key_result = keystore.handle_key_args(args)
    if key_result is not None:
        return key_result

    if args.check_jev:
        import jev
        result = jev.check()
        if result["ok"]:
            u = result.get("usage") or {}
            cost = (u.get("input_tokens", 0) * 0.042) / 1_000_000
            print(f"Jev OK -- {result['detail']}")
            print(f"  key source      : {result['key_source']}")
            print(f"  model requested : {result['model_requested']}")
            print(f"  model resolved  : {result['model_resolved']}")
            if result["model_resolved"] and result["model_resolved"] != result["model_requested"]:
                print("  WARNING: resolved version differs from the pin -- any "
                      "calibrated thresholds are suspect")
            print(f"  usage           : {u.get('input_tokens')} in / "
                  f"{u.get('output_tokens')} out  (~${cost:.7f})")
            return 0
        print(f"Jev NOT working ({result['stage']}): {result['detail']}", file=sys.stderr)
        if result["stage"] == "key":
            print("  fix: python probe.py --set-api-key apikey_...", file=sys.stderr)
        return 1

    repo_root = Path(args.repo).resolve() if args.repo else None
    reg = load_cached(args.refresh, repo_root, not args.no_version,
                      want_auth=not args.no_verify_auth)

    if args.json:
        json.dump(reg, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(summarize(reg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
