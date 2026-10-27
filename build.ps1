# YD Browser build script
# Usage: powershell -ExecutionPolicy Bypass -File .\build.ps1
# Result: dist\YD_Browser\YD_Browser.exe and dist\YD_Browser_v<version>.zip (+ .sha256)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$script = Get-ChildItem -Filter "secure_browser v*.py" | Sort-Object Name | Select-Object -Last 1
if (-not $script) { throw "secure_browser v*.py not found" }
$match = [regex]::Match((Get-Content $script.FullName -Raw -Encoding UTF8), 'VERSION = "([\d.]+)"')
if (-not $match.Success) { throw "VERSION not found in $($script.Name)" }
$ver = $match.Groups[1].Value
if ($script.Name -ne "secure_browser v$ver.py") { throw "File name ($($script.Name)) and VERSION ($ver) do not match" }
Write-Host "Building v$ver from $($script.Name)"

# Windows version info for the exe (shown in file properties)
$parts = @($ver.Split('.') | ForEach-Object { [int]$_ })
while ($parts.Count -lt 4) { $parts += 0 }
$tuple = $parts -join ', '
New-Item -ItemType Directory -Force build | Out-Null
$versionFile = Join-Path $PSScriptRoot "build\version_info.txt"
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041204b0', [
      StringStruct('CompanyName', 'KIM YEONGDO'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 KIM YEONGDO'),
      StringStruct('FileDescription', 'YD Browser'),
      StringStruct('FileVersion', '$ver'),
      StringStruct('InternalName', 'YD_Browser'),
      StringStruct('OriginalFilename', 'YD_Browser.exe'),
      StringStruct('ProductName', 'YD Browser'),
      StringStruct('ProductVersion', '$ver')])]),
    VarFileInfo([VarStruct('Translation', [1042, 1200])])
  ]
)
"@ | Set-Content -Path $versionFile -Encoding UTF8

if (Test-Path dist\YD_Browser) { Remove-Item -Recurse -Force dist\YD_Browser }
# PyInstaller prints progress to stderr, which PowerShell 5.1 treats as an error when "Stop" is set
$ErrorActionPreference = "Continue"
python -m PyInstaller --noconfirm --windowed --name YD_Browser `
    --icon (Join-Path $PSScriptRoot "icon.ico") `
    --add-data "$(Join-Path $PSScriptRoot 'icon.ico');." `
    --version-file $versionFile `
    --distpath dist --workpath build --specpath build `
    $script.FullName
$exit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($exit -ne 0) { throw "PyInstaller failed (exit $exit)" }

$zip = "dist\YD_Browser_v$ver.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\YD_Browser -DestinationPath $zip
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
"$hash  YD_Browser_v$ver.zip" | Set-Content -Path "$zip.sha256" -Encoding ASCII
Write-Host "Done: $zip"
Write-Host "SHA256: $hash"
