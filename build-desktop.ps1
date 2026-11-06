param([string]$Version)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskVersionArgs = @('.\tools\package_release.py', '--prepare')
if ($Version) { $taskVersionArgs += @('--version', $Version) }
$taskReleaseVersion = & '.\.venv\Scripts\python.exe' @taskVersionArgs
if ($LASTEXITCODE -ne 0) { throw 'Release version preparation failed.' }
$taskReleaseVersion = $taskReleaseVersion.Trim()
$taskReleaseName = "Mail-Merger-v$taskReleaseVersion-Windows-x64"
$taskOutput = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "dist\$taskReleaseName"))
if (-not $taskOutput.StartsWith($PSScriptRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Unexpected desktop output directory.'
}
if (Get-Process -Name 'Mail-Merger' -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and [System.IO.Path]::GetDirectoryName($_.Path) -eq $taskOutput
}) {
    throw 'Close the packaged Mail-Merger app before replacing its files. No build files were removed.'
}
if (Test-Path -LiteralPath $taskOutput) {
    throw 'This version already exists. Choose a newer release version.'
}
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm 'Mail-Merger.spec'
if ($LASTEXITCODE -ne 0) { throw 'Desktop packaging failed.' }
Copy-Item -LiteralPath '.\README.md' -Destination (Join-Path $taskOutput 'README.md') -Force
& '.\.venv\Scripts\python.exe' '.\tools\package_release.py'
if ($LASTEXITCODE -ne 0) { throw 'Portable ZIP verification failed.' }
Write-Host "Built dist\$taskReleaseName\Mail-Merger.exe and dist\$taskReleaseName.zip."
