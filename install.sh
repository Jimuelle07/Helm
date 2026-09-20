#!/usr/bin/env bash
# Install the Helm skill into a coding agent's skills directory.
#
# SKILL.md is an agent-neutral format, so the same folder works in Codex,
# Cursor, OpenCode, Claude Code and anything else that reads Agent Skills.
# All this script does is put skills/helm where a given agent looks for it.
#
#   ./install.sh                 # -> ~/.agents/skills  (the shared location)
#   ./install.sh codex cursor    # -> that agent's own directory as well
#   ./install.sh --copy          # copy instead of symlink (no git pull updates)
#   ./install.sh --uninstall     # remove whatever this script installed
#   ./install.sh --list          # show where Helm is currently installed
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/skills/helm"

target_dir() {
  case "$1" in
    agents)   printf '%s\n' "$HOME/.agents/skills" ;;
    codex)    printf '%s\n' "${CODEX_HOME:-$HOME/.codex}/skills" ;;
    cursor)   printf '%s\n' "$HOME/.cursor/skills" ;;
    opencode) printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills" ;;
    claude)   printf '%s\n' "$HOME/.claude/skills" ;;
    project)  printf '%s\n' "$PWD/.agents/skills" ;;
    *)        return 1 ;;
  esac
}

ALL_TARGETS="agents codex cursor opencode claude project"
targets=()
mode=install
link=1

for arg in "$@"; do
  case "$arg" in
    --copy)      link=0 ;;
    --uninstall) mode=uninstall ;;
    --list)      mode=list ;;
    -h|--help)   sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)          echo "unknown option: $arg" >&2; exit 2 ;;
    *)
      if ! target_dir "$arg" >/dev/null; then
        echo "unknown agent: $arg (choose from: $ALL_TARGETS)" >&2
        exit 2
      fi
      targets+=("$arg")
      ;;
  esac
done

if [ "$mode" = list ]; then
  for name in $ALL_TARGETS; do
    dest="$(target_dir "$name")/helm"
    if [ -e "$dest" ] || [ -L "$dest" ]; then
      kind=$([ -L "$dest" ] && echo "linked" || echo "copied")
      printf '  %-9s %s (%s)\n' "$name" "$dest" "$kind"
    fi
  done
  exit 0
fi

[ ${#targets[@]} -eq 0 ] && targets=(agents)

[ -f "$SRC/SKILL.md" ] || { echo "no skill found at $SRC" >&2; exit 1; }

for name in "${targets[@]}"; do
  dir="$(target_dir "$name")"
  dest="$dir/helm"

  if [ "$mode" = uninstall ]; then
    if [ -e "$dest" ] || [ -L "$dest" ]; then
      rm -rf "$dest"
      echo "removed   $dest"
    else
      echo "not there $dest"
    fi
    continue
  fi

  mkdir -p "$dir"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    rm -rf "$dest"
  fi

  if [ "$link" = 1 ]; then
    ln -s "$SRC" "$dest" 2>/dev/null || true
  fi

  if [ -L "$dest" ]; then
    echo "linked    $dest -> $SRC"
  else
    # No symlink support (plain Windows shells, some filesystems): fall back to
    # a copy, which works identically but does not follow a later git pull.
    rm -rf "$dest"
    cp -R "$SRC" "$dest"
    echo "copied    $dest"
    echo "          (no symlink here; re-run this script after a git pull)"
  fi
done

echo
echo "Restart the agent, then ask it what coding agents are installed on this machine."
