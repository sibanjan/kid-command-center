<#
Build the release zip: dist\KidCommandCenter-<version>.zip with the app's wheel, pinned
library versions (what the tests ran on), release.json and a stamped install.ps1.

    powershell -ExecutionPolicy Bypass -File installer\build.ps1
#>
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $repo ".venv\Scripts\python.exe"
$version = (& $py -c "import tomllib; print(tomllib.load(open(r'$repo\pyproject.toml','rb'))['project']['version'])").Trim()
$pyVersion = (& $py -c "import platform; print(platform.python_version())").Trim()
$name = "KidCommandCenter-$version"
$out = Join-Path $repo "dist\$name"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force $out | Out-Null

Write-Host "==> Wheel"
$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& $py -m pip wheel $repo --no-deps --quiet -w $out 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -notmatch "notice" }
$ErrorActionPreference = $old
if ($LASTEXITCODE -ne 0) { throw "building the wheel failed" }
$wheel = (Get-ChildItem $out -Filter "kid_command_center-*.whl").Name

Write-Host "==> Pinned versions"
$constraints = "constraints-$version.txt"
& $py -m pip freeze --exclude-editable --disable-pip-version-check |
    Where-Object { $_ -notmatch '^(pytest|ruff|httpx|iniconfig|pluggy|pygments|packaging)==' } |
    Set-Content -Encoding ASCII (Join-Path $out $constraints)

@{ version = $version; python = $pyVersion; wheel = $wheel; constraints = $constraints } |
    ConvertTo-Json | Set-Content -Encoding ASCII (Join-Path $out "release.json")

# ASCII, no BOM: the script is run with iex, which breaks on a BOM.
$script = (Get-Content (Join-Path $PSScriptRoot "install.ps1") -Raw).Replace("__APP_VERSION__", $version).Replace("__PYTHON_VERSION__", $pyVersion)
[IO.File]::WriteAllText((Join-Path $out "install.ps1"), $script, (New-Object Text.ASCIIEncoding))

$zip = Join-Path $repo "dist\$name.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path (Join-Path $out "*") -DestinationPath $zip
Write-Host "==> Done: $zip" -ForegroundColor Green
Get-ChildItem $out | ForEach-Object { "    {0,-50} {1,8:N0} KB" -f $_.Name, ($_.Length / 1KB) }
