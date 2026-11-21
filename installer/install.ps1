<#
Kid Command Center install. On HIS laptop, signed in as HIM:

  1. Copy either the kid-command-center folder (the source) or an extracted release zip
     (KidCommandCenter-<version>.zip) to the laptop.
  2. Open PowerShell in that folder (right-click the folder > Open in Terminal).
  3. Run:   iex (Get-Content .\installer\install.ps1 -Raw)     # from the source folder
     or:    iex (Get-Content .\install.ps1 -Raw)               # from an extracted release zip

No admin rights needed. It unpacks the signed Python runtime from python.org's NuGet package,
installs the app from this folder (its libraries come from PyPI), adds a Start menu entry for
the parent window, starts monitoring at sign-in, and opens the window so you can set the parent
password. Running it again upgrades in place; that asks for the parent password first.

Options, as environment variables set before running it:
    $env:KCC_DIR = "D:\Apps\KidCommandCenter"   # where to install the program
    $env:KCC_FROM = "C:\path\to\extracted\zip"  # where the release files are (default: this folder)
#>

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Stamped by installer\build.ps1.
$KccVersion = "__APP_VERSION__"
$KccPython = "__PYTHON_VERSION__"

function Install-KidCommandCenter {
    $appName = "Kid Command Center"
    $dir = if ($env:KCC_DIR) { $env:KCC_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\KidCommandCenter" }
    $from = if ($env:KCC_FROM) { $env:KCC_FROM } else { (Get-Location).Path }
    $manifestPath = Join-Path $from "release.json"
    $source = $null
    if (-not (Test-Path $manifestPath)) {
        # Not a release folder: install straight from a copy of the source (the repo folder or
        # its installer folder).
        $source = @($from, (Split-Path $from -Parent)) | Where-Object { $_ -and (Test-Path (Join-Path $_ "pyproject.toml")) } | Select-Object -First 1
        if (-not $source) {
            throw "Run this from the kid-command-center folder (the one with pyproject.toml), or from an extracted release zip."
        }
        $m = Select-String -Path (Join-Path $source "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
        $KccVersion = if ($m) { $m.Matches[0].Groups[1].Value } else { "source" }
    }
    if ($KccPython.StartsWith("__")) { $KccPython = "3.13.14" }
    Write-Host "Installing Kid Command Center $KccVersion for $env:USERNAME into $dir" -ForegroundColor Cyan
    Write-Host "It records and manages what is opened in THIS Windows account. Install it signed in as your child."

    $work = Join-Path $env:TEMP ("KccInstall-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force $work, $dir | Out-Null
    function Invoke-Checked([string]$exe, [string[]]$argv) {
        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { & $exe @argv 2>&1 | ForEach-Object { if ("$_".Trim()) { "    $_" } } } finally { $ErrorActionPreference = $old }
        if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($exe)) $($argv[0..2] -join ' ') ... failed (exit code $LASTEXITCODE)" }
    }

    try {
        $runtime = Join-Path $dir "runtime"
        $py = Join-Path $runtime "python.exe"
        $pyw = Join-Path $runtime "pythonw.exe"
        if (Test-Path $py) {
            Write-Host "Kid Command Center is already installed. To upgrade it, enter the parent password."
            & $py -m kid_command_center stop
            if ($LASTEXITCODE -ne 0) { throw "Not upgraded: the parent password is needed." }
        }

        if ($source) {
            $wheel = $source
            $constraints = Join-Path $source "installer\constraints.txt"
        }
        else {
            $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
            $wheel = Join-Path $from $manifest.wheel
            $constraints = Join-Path $from $manifest.constraints
        }

        Write-Host "[1/3] Downloading Python $KccPython..."
        $pkg = Join-Path $work "python.zip"
        Invoke-WebRequest -UseBasicParsing "https://www.nuget.org/api/v2/package/python/$KccPython" -OutFile $pkg
        Expand-Archive $pkg (Join-Path $work "python")
        if (Test-Path $runtime) { Remove-Item -Recurse -Force $runtime }
        Move-Item (Join-Path $work "python\tools") $runtime

        Write-Host "[2/3] Installing Kid Command Center and its libraries..."
        Invoke-Checked $py @("-m", "ensurepip", "--upgrade")
        $pin = if (Test-Path $constraints) { @("-c", $constraints) } else { @() }
        Invoke-Checked $py (@("-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check",
                              "--quiet") + $pin + @($wheel))

        Write-Host "[3/3] Adding the Start menu entry and starting monitoring at sign-in..."
        $lnk = Join-Path ([Environment]::GetFolderPath("Programs")) "$appName.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $s = $shell.CreateShortcut($lnk)
        $s.TargetPath = $pyw; $s.Arguments = "-m kid_command_center.app"; $s.WorkingDirectory = $dir
        $s.Description = "Parent window (password protected)"
        $s.Save()
        Invoke-Checked $py @("-m", "kid_command_center", "startup", "on")
        Set-Content -Encoding UTF8 (Join-Path $dir "uninstall.ps1") (Get-UninstallScript $dir $lnk)
    }
    finally {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }

    Start-Process $pyw -ArgumentList "-m kid_command_center run" -WorkingDirectory $dir
    Start-Sleep -Seconds 3
    Start-Process $pyw -ArgumentList "-m kid_command_center.app" -WorkingDirectory $dir
    Write-Host ""
    Write-Host "Kid Command Center is installed and monitoring." -ForegroundColor Green
    Write-Host "The parent window is open: set the parent password now. Later, open it from the Start menu ($appName)."
    Write-Host "To remove it: iex (Get-Content `"$dir\uninstall.ps1`" -Raw)   (asks for the parent password)"
}

function Get-UninstallScript([string]$dir, [string]$lnk) {
    # Plain cmdlets only, so it also works where PowerShell runs in Constrained Language mode.
    return @"
`$dir = "$dir"
& "`$dir\runtime\python.exe" -m kid_command_center stop
if (`$LASTEXITCODE -ne 0) { Write-Host "Not removed: the parent password is needed."; return }
Remove-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "KidCommandCenter" -ErrorAction SilentlyContinue
Remove-Item "$lnk" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Remove-Item "`$dir" -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Kid Command Center is removed. Its activity history and password are kept in `$env:LOCALAPPDATA\KidCommandCenter; delete that folder to remove them too."
"@
}

Install-KidCommandCenter
