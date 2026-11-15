# YD Browser build script
# Usage: powershell -ExecutionPolicy Bypass -File .\build.ps1
# Result: dist\YD_Browser\YD_Browser.exe (+ WebView2 DLLs) and dist\YD_Browser_v<version>.zip (+ .sha256)
# Version: src\Program.cs AssemblyVersion / AssemblyFileVersion. Compiler: csc of .NET Framework 4.x in Windows (C# 5 only).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "64-bit .NET Framework 4.x compiler not found" }
$out = "dist\YD_Browser"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force $out | Out-Null
& $csc /nologo /codepage:65001 /target:winexe /platform:x64 /optimize+ /win32icon:icon.ico /out:$out\YD_Browser.exe `
  /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /r:System.Security.dll `
  /r:lib\Microsoft.Web.WebView2.Core.dll /r:lib\Microsoft.Web.WebView2.WinForms.dll src\*.cs
if ($LASTEXITCODE -ne 0) { throw "compile failed" }
Copy-Item lib\Microsoft.Web.WebView2.Core.dll, lib\Microsoft.Web.WebView2.WinForms.dll, lib\WebView2Loader.dll, lib\WebView2_LICENSE.txt $out

# self test - stop if it fails
$p = Start-Process "$out\YD_Browser.exe" -ArgumentList "--selftest" -Wait -PassThru -NoNewWindow -RedirectStandardOutput selftest_out.txt
Get-Content selftest_out.txt -Encoding utf8 | Select-Object -Last 1
if ($p.ExitCode -ne 0) { Get-Content selftest_out.txt -Encoding utf8 | Select-String "FAIL"; throw "self test failed" }

$v = [version](Get-Item "$out\YD_Browser.exe").VersionInfo.FileVersion
$ver = "$($v.Major).$($v.Minor)"
if ($v.Build -gt 0) { $ver += ".$($v.Build)" }
$zip = "dist\YD_Browser_v$ver.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out -DestinationPath $zip
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
"$hash  YD_Browser_v$ver.zip" | Set-Content -Path "$zip.sha256" -Encoding ASCII
Write-Host "Done: $zip"
Write-Host "SHA256: $hash"