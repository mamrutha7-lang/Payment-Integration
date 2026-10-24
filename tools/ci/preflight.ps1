#Requires -Version 5.1
#
# tools/ci/preflight.ps1 - run tools/ci/preflight.sh from PowerShell.
#
# WHY THIS IS A WRAPPER AND NOT A SECOND IMPLEMENTATION. The four checks that
# gate main are defined once, in .github/workflows/ci.yml, and reproduced once,
# in tools/ci/preflight.sh. A PowerShell port would be a third copy of the same
# four commands, and the copy that drifts from ci.yml is always the one nobody
# runs often enough to notice - which is worse than having no local runner at
# all, because it answers "green" about a pipeline it no longer describes. So
# this file does exactly one thing: find bash and hand over, forwarding every
# argument and the exit code unchanged.
#
# Usage (arguments are passed straight through to preflight.sh):
#   .\tools\ci\preflight.ps1
#   .\tools\ci\preflight.ps1 --quick
#   .\tools\ci\preflight.ps1 --keep-going --clean-deps
#
# Exit codes are preflight.sh's: 0 all green, 1 something failed, 2 something
# did not run.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script = (Join-Path $PSScriptRoot 'preflight.sh') -replace '\\', '/'

if (-not (Test-Path -LiteralPath $script)) {
  Write-Error "preflight: cannot find $script next to this wrapper."
  exit 66
}

function Find-Bash {
  # 1. An explicit override wins. Set REEP_BASH if bash lives somewhere odd.
  if ($env:REEP_BASH -and (Test-Path -LiteralPath $env:REEP_BASH)) {
    return $env:REEP_BASH
  }

  # 2. Git for Windows, located from git itself. This is the bash the repo
  #    already assumes exists - tools/fonts/fetch-fonts.sh is a bash script and
  #    AGENTS.md's setup commands are POSIX paths - so if git is installed the
  #    right interpreter is two directories away from it:
  #    ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
  $git = Get-Command git -ErrorAction SilentlyContinue
  if ($git) {
    $gitRoot = Split-Path -Parent (Split-Path -Parent $git.Source)
    foreach ($rel in @('bin\bash.exe', 'usr\bin\bash.exe')) {
      $candidate = Join-Path $gitRoot $rel
      if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
  }

  # 3. The usual install locations, for a git that is not on PATH.
  $guesses = @(
    (Join-Path $env:ProgramFiles 'Git\bin\bash.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Git\bin\bash.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe')
  )
  foreach ($candidate in $guesses) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
  }

  # 4. Anything called bash on PATH - EXCEPT System32\bash.exe, which is the
  #    WSL launcher. That one runs inside a Linux filesystem view where
  #    apps/api-py/.venv/Scripts/python.exe is a Windows binary the script has
  #    no business invoking, and the failure it produces reads as a broken
  #    preflight rather than as the wrong shell.
  $onPath = Get-Command bash -ErrorAction SilentlyContinue
  if ($onPath) {
    $system32 = Join-Path $env:WINDIR 'System32'
    if (-not $onPath.Source.StartsWith($system32, [System.StringComparison]::OrdinalIgnoreCase)) {
      return $onPath.Source
    }
  }

  return $null
}

$bash = Find-Bash

if (-not $bash) {
  Write-Host ''
  Write-Host 'preflight: no bash found, so the local runner cannot start.' -ForegroundColor Yellow
  Write-Host ''
  Write-Host 'Install Git for Windows (it ships the bash this repo already needs for'
  Write-Host 'tools/fonts/fetch-fonts.sh), then run this again:'
  Write-Host ''
  Write-Host '    winget install --id Git.Git -e'
  Write-Host ''
  Write-Host 'Or set REEP_BASH to a bash.exe you already have:'
  Write-Host ''
  Write-Host '    $env:REEP_BASH = "C:\Program Files\Git\bin\bash.exe"'
  Write-Host ''
  Write-Host 'Or run the five checks by hand. They are the five CI jobs, they are the'
  Write-Host 'five REQUIRED status checks on main, and they are the only thing'
  Write-Host 'preflight.sh does:'
  Write-Host ''
  Write-Host '    cd apps\api-py'
  Write-Host '    .venv\Scripts\python ..\..\tools\ci\check_api_imports.py   # API (dependency completeness)'
  Write-Host ''
  Write-Host '    cd ..\..'
  Write-Host '    python tools\ci\check_pii_gate.py                         # Rule 1 (every model call declares its cargo)'
  Write-Host ''
  Write-Host '    cd infra\cdk'
  Write-Host '    .venv\Scripts\python -m pytest -q                          # Infra (CDK synth guards)'
  Write-Host ''
  Write-Host '    cd ..\..'
  Write-Host '    python tools\ci\check_brand_magenta.py'
  Write-Host '    python tools\ci\check_style_duplicates.py'
  Write-Host '    python tools\ci\check_theme_tokens.py'
  Write-Host '    cd apps\web'
  Write-Host '    npx tsc --noEmit -p tsconfig.app.json'
  Write-Host '    npx ng test --watch=false'
  Write-Host '    npx ng build                                              # Web (Angular)'
  Write-Host ''
  Write-Host '    cd ..\api-py'
  Write-Host '    .venv\Scripts\python -m alembic upgrade head'
  Write-Host '    .venv\Scripts\python -m app.seed'
  Write-Host '    $env:REEP_REQUIRE_DB = "1"; .venv\Scripts\python -m pytest -q   # API (FastAPI + Postgres)'
  Write-Host ''
  Write-Host 'The pytest one needs Postgres: docker compose up -d'
  Write-Host 'The CDK one needs its OWN venv under infra\cdk on Python 3.12, not apps\api-py\.venv:'
  Write-Host 'the cdk job pins 3.12 and aws-cdk-lib is in neither api requirements file.'
  Write-Host ''
  exit 69
}

& $bash $script @args
exit $LASTEXITCODE
