<#
.SYNOPSIS
  Builds the cotaforge Windows installer (dist\cotaforge-<version>-setup.exe).

.DESCRIPTION
  1. Downloads a pinned, relocatable CPython 3.12 (python-build-standalone, install_only flavour, with tkinter) and
     verifies its SHA256.
  2. Builds wheels of cotaforge-kernel and cotaforge-mcp and installs them, with all their dependencies, into that
     Python. The result runs offline and from any folder: nothing depends on the build path (no Scripts\*.exe
     launchers are kept; everything runs as `python.exe -m ...`).
  3. Copies the plugin and the marketplace file next to it, byte-compiles the packages and runs a smoke test from the
     staged copy.
  4. Calls Inno Setup (ISCC) to compile cotaforge.iss. Without ISCC it stops after staging (build\stage), which is
     enough to test the layout locally.

  Needs internet (python-build-standalone and PyPI) and Windows' own tar.exe. build\ and dist\ are git-ignored.

.PARAMETER Version
  Expected version. Defaults to the one in mcp-server\pyproject.toml; the build fails if the kernel, the plugin or
  the marketplace disagree, or if -Version is given and differs.

.PARAMETER Iscc
  Path of ISCC.exe. By default it is looked up on the PATH and in the usual Inno Setup folders.

.PARAMETER SkipInstaller
  Stage only, even if ISCC is available.

.PARAMETER Clean
  Delete build\ first.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Iscc = "",
    [switch]$SkipInstaller,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- pins ----------------------------------------------------------------------------------------------------------
# python-build-standalone release 20261026, CPython 3.12.16, Windows x86_64, install_only (the SHA256 is the one in the
# release's SHA256SUMS). To move to another Python: change the three values together and re-check the hash.
$PbsRelease = "20261026"
$PbsFile = "cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only.tar.gz"
$PbsSha256 = "590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5"

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Build = Join-Path $Repo "build"
$Downloads = Join-Path $Build "downloads"
$Wheels = Join-Path $Build "wheels"
$Stage = Join-Path $Build "stage"
$Dist = Join-Path $Repo "dist"

function Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Invoke-Checked {
    # Runs a native command and fails on a non-zero exit code. Arguments are passed as an array (paths with spaces).
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $Exe $($Arguments -join ' ')" }
}

# --- versions ------------------------------------------------------------------------------------------------------
function Get-PyprojectVersion([string]$Path) {
    $line = Select-String -Path $Path -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $line) { throw "No version in $Path" }
    return $line.Matches[0].Groups[1].Value
}

$KernelVersion = Get-PyprojectVersion (Join-Path $Repo "kernel\pyproject.toml")
$McpVersion = Get-PyprojectVersion (Join-Path $Repo "mcp-server\pyproject.toml")
$PluginVersion = (Get-Content (Join-Path $Repo "plugin\.claude-plugin\plugin.json") -Raw | ConvertFrom-Json).version
$MarketplaceFile = Join-Path $Repo ".claude-plugin\marketplace.json"
$MarketplaceVersion = (Get-Content $MarketplaceFile -Raw | ConvertFrom-Json).plugins[0].version
$All = @($KernelVersion, $McpVersion, $PluginVersion, $MarketplaceVersion)
if (@($All | Select-Object -Unique).Count -ne 1) {
    throw "Versions disagree: kernel $KernelVersion, mcp-server $McpVersion, plugin $PluginVersion, marketplace $MarketplaceVersion"
}
if ($Version -and $Version -ne $McpVersion) { throw "-Version $Version does not match the packages ($McpVersion)" }
$Version = $McpVersion
Step "cotaforge $Version"

if ($Clean -and (Test-Path $Build)) { Remove-Item $Build -Recurse -Force }
foreach ($dir in @($Downloads, $Wheels)) { New-Item -ItemType Directory -Force $dir | Out-Null }
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
if (Test-Path $Wheels) { Get-ChildItem $Wheels -File | Remove-Item -Force }
New-Item -ItemType Directory -Force $Stage | Out-Null

# --- python-build-standalone ---------------------------------------------------------------------------------------
Step "Python $PbsFile"
$Archive = Join-Path $Downloads $PbsFile
$HaveGood = (Test-Path $Archive) -and ((Get-FileHash $Archive -Algorithm SHA256).Hash -ieq $PbsSha256)
if (-not $HaveGood) {
    $url = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsRelease/" + $PbsFile.Replace("+", "%2B")
    Write-Host "downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $Archive -UseBasicParsing
    $actual = (Get-FileHash $Archive -Algorithm SHA256).Hash
    if ($actual -ine $PbsSha256) {
        Remove-Item $Archive -Force
        throw "SHA256 mismatch for $PbsFile`: expected $PbsSha256, got $actual"
    }
}
Write-Host "SHA256 verified: $PbsSha256"

# Windows' own bsdtar: the tar of Git for Windows cannot read "C:\..." paths.
$Tar = Join-Path $env:SystemRoot "System32\tar.exe"
Invoke-Checked $Tar @("-xzf", $Archive, "-C", $Stage)   # the archive's top folder is python\
$Python = Join-Path $Stage "python\python.exe"
if (-not (Test-Path $Python)) { throw "python.exe not found after extracting: $Python" }

# Debug symbols (several MB each) are of no use to the user.
Get-ChildItem (Join-Path $Stage "python") -Filter "*.pdb" -File | Remove-Item -Force

# A marker like this would make pip refuse to install; python-build-standalone does not ship one. Removed only if present.
$Marker = Join-Path $Stage "python\Lib\EXTERNALLY-MANAGED"
if (Test-Path $Marker) { Write-Host "removing $Marker"; Remove-Item $Marker -Force }

# --- wheels and dependencies ---------------------------------------------------------------------------------------
Step "Building wheels"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
Invoke-Checked $Python @("-I", "-m", "pip", "wheel", "--no-deps", "--wheel-dir", $Wheels, (Join-Path $Repo "kernel"), (Join-Path $Repo "mcp-server"))

Step "Installing the packages and their dependencies into the staged Python"
Invoke-Checked $Python @("-I", "-m", "pip", "install", "--no-warn-script-location", "--no-compile", "--find-links", $Wheels, "cotaforge-kernel==$Version", "cotaforge-mcp==$Version")
# The record of what went in (for diagnosing a report), shipped next to the program.
& $Python -I -m pip freeze --all | Set-Content (Join-Path $Stage "packages.txt") -Encoding ascii
if ($LASTEXITCODE -ne 0) { throw "pip freeze failed" }

# The licenses: cotaforge's own, and the list of everything else that is bundled (read from what pip just installed).
# Inno Setup copies the whole stage, so both end up in the installed folder.
Copy-Item (Join-Path $Repo "LICENSE") (Join-Path $Stage "LICENSE")
Invoke-Checked $Python @("-I", (Join-Path $PSScriptRoot "third_party_notices.py"), "--site-packages", (Join-Path $Stage "python\Lib\site-packages"), "--out", (Join-Path $Stage "THIRD_PARTY_NOTICES.md"))

# The launchers in Scripts\ embed the absolute path of this build: nothing may use them. Everything runs as `python -m`.
$Scripts = Join-Path $Stage "python\Scripts"
if (Test-Path $Scripts) { Remove-Item $Scripts -Recurse -Force }

# --- plugin, marketplace, manual -----------------------------------------------------------------------------------
Step "Plugin, marketplace and manual"
$Market = Join-Path $Stage "marketplace"
New-Item -ItemType Directory -Force (Join-Path $Market ".claude-plugin") | Out-Null
Copy-Item $MarketplaceFile (Join-Path $Market ".claude-plugin\marketplace.json")
Copy-Item (Join-Path $Repo "plugin") (Join-Path $Market "plugin") -Recurse
Get-ChildItem (Join-Path $Market "plugin") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
New-Item -ItemType Directory -Force (Join-Path $Stage "docs") | Out-Null
foreach ($manual in "manual-usuario.md", "user-manual.md") {
  Copy-Item (Join-Path $Repo "docs\$manual") (Join-Path $Stage "docs\$manual")
}
foreach ($icon in "cotaforge.ico", "cotaforge-new.ico", "cotaforge-open.ico") {
  Copy-Item (Join-Path $PSScriptRoot $icon) (Join-Path $Stage $icon)
}

# --- byte-compile --------------------------------------------------------------------------------------------------
Step "Byte-compiling the standard library and site-packages"
# checked-hash: the byte-code stays valid whatever the timestamps are after the installer copies the files. Done
# before the smoke test so that nothing is compiled lazily (and recorded with the build path) by running it.
Invoke-Checked $Python @("-I", "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "checked-hash", (Join-Path $Stage "python\Lib"))

# --- smoke from the stage ------------------------------------------------------------------------------------------
Step "Smoke test of the staged copy (fake CAD)"
Invoke-Checked $Python @("-I", (Join-Path $PSScriptRoot "smoke.py"), "--adapter", "fake")
Invoke-Checked $Python @("-I", "-c", "import tkinter; tkinter.Tcl(); print('ok: tkinter and Tcl load')")

$StageBytes = (Get-ChildItem $Stage -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("staged: {0} ({1:N0} MB)" -f $Stage, ($StageBytes / 1MB))

# --- installer -----------------------------------------------------------------------------------------------------
if ($SkipInstaller) { Write-Host "-SkipInstaller: stopping after staging"; exit 0 }

if (-not $Iscc) {
    $found = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($found) { $Iscc = $found.Source }
    else {
        $roots = @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA "Programs")) | Where-Object { $_ }
        foreach ($root in $roots) {
            $candidate = Join-Path $root "Inno Setup 6\ISCC.exe"
            if (Test-Path $candidate) { $Iscc = $candidate; break }
        }
    }
}
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    Write-Host "ISCC.exe not found: staged only (build\stage). Install Inno Setup 6 or pass -Iscc to build the .exe."
    exit 0
}

Step "Compiling the installer with $Iscc"
New-Item -ItemType Directory -Force $Dist | Out-Null
Invoke-Checked $Iscc @("/Qp", "/DAppVersion=$Version", "/DSourceDir=$Stage", "/DOutputDir=$Dist", (Join-Path $PSScriptRoot "cotaforge.iss"))
$Setup = Join-Path $Dist "cotaforge-$Version-setup.exe"
if (-not (Test-Path $Setup)) { throw "The installer was not produced: $Setup" }
Write-Host ("built: {0} ({1:N0} MB)" -f $Setup, ((Get-Item $Setup).Length / 1MB))
