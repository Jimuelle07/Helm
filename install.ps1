<#
.SYNOPSIS
  Install the Helm skill into a coding agent's skills directory.

.DESCRIPTION
  SKILL.md is an agent-neutral format, so the same folder works in Codex,
  Cursor, OpenCode, Claude Code and anything else that reads Agent Skills.
  All this script does is put skills\helm where a given agent looks for it.

.EXAMPLE
  .\install.ps1                  # -> ~\.agents\skills  (the shared location)
.EXAMPLE
  .\install.ps1 codex cursor     # -> that agent's own directory as well
.EXAMPLE
  .\install.ps1 -Copy            # copy instead of a junction
.EXAMPLE
  .\install.ps1 -Uninstall       # remove whatever this script installed
.EXAMPLE
  .\install.ps1 -List            # show where Helm is currently installed
#>
[CmdletBinding()]
param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $Agents,
  [switch] $Copy,
  [switch] $Uninstall,
  [switch] $List
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root 'skills\helm'
$all = @('agents', 'codex', 'cursor', 'opencode', 'claude', 'project')

function Get-TargetDir([string] $name) {
  switch ($name) {
    'agents' { return (Join-Path $HOME '.agents\skills') }
    'codex' {
      $codexHome = $env:CODEX_HOME
      if (-not $codexHome) { $codexHome = Join-Path $HOME '.codex' }
      return (Join-Path $codexHome 'skills')
    }
    'cursor' { return (Join-Path $HOME '.cursor\skills') }
    'opencode' {
      $cfg = $env:XDG_CONFIG_HOME
      if (-not $cfg) { $cfg = Join-Path $HOME '.config' }
      return (Join-Path $cfg 'opencode\skills')
    }
    'claude' { return (Join-Path $HOME '.claude\skills') }
    'project' { return (Join-Path (Get-Location) '.agents\skills') }
    default { throw "unknown agent: $name (choose from: $($all -join ', '))" }
  }
}

function Test-IsLink([string] $path) {
  $item = Get-Item -LiteralPath $path -Force
  return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

if ($List) {
  foreach ($name in $all) {
    $dest = Join-Path (Get-TargetDir $name) 'helm'
    if (Test-Path -LiteralPath $dest) {
      if (Test-IsLink $dest) { $kind = 'linked' } else { $kind = 'copied' }
      '  {0,-9} {1} ({2})' -f $name, $dest, $kind
    }
  }
  return
}

$targets = $Agents | Where-Object { $_ }
if (-not $targets) { $targets = @('agents') }
foreach ($name in $targets) { Get-TargetDir $name | Out-Null }

if (-not (Test-Path -LiteralPath (Join-Path $src 'SKILL.md'))) {
  throw "no skill found at $src"
}

foreach ($name in $targets) {
  $dir = Get-TargetDir $name
  $dest = Join-Path $dir 'helm'

  if ($Uninstall) {
    if (Test-Path -LiteralPath $dest) {
      Remove-Item -LiteralPath $dest -Recurse -Force
      "removed   $dest"
    }
    else {
      "not there $dest"
    }
    continue
  }

  if (-not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  if (Test-Path -LiteralPath $dest) {
    Remove-Item -LiteralPath $dest -Recurse -Force
  }

  # A junction, unlike a symlink, needs no administrator rights and no
  # developer mode -- and it follows a later git pull, which a copy does not.
  $linked = $false
  if (-not $Copy) {
    try {
      New-Item -ItemType Junction -Path $dest -Target $src | Out-Null
      $linked = $true
    }
    catch {
      $linked = $false
    }
  }

  if ($linked) {
    "linked    $dest -> $src"
  }
  else {
    Copy-Item -LiteralPath $src -Destination $dest -Recurse -Force
    "copied    $dest"
    if (-not $Copy) { "          (junction unavailable; re-run this script after a git pull)" }
  }
}

if (-not $Uninstall) {
  ''
  'Restart the agent, then ask it what coding agents are installed on this machine.'
}
