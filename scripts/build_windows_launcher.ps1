$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$Launcher = Join-Path $RootDir "scripts\windows_launcher.py"
$OutputDir = Join-Path $RootDir "dist"

Set-Location $RootDir

$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
python -m pip show pyinstaller *> $null
$PyInstallerMissing = $LASTEXITCODE -ne 0
$ErrorActionPreference = $PreviousErrorActionPreference

if ($PyInstallerMissing) {
  python -m pip install pyinstaller
}

python -m PyInstaller `
  --clean `
  --onefile `
  --name PDFCraftLauncher `
  --distpath $OutputDir `
  --workpath (Join-Path $RootDir "build\pyinstaller") `
  --specpath (Join-Path $RootDir "build\pyinstaller") `
  $Launcher

Write-Host ""
Write-Host "Built launcher:" (Join-Path $OutputDir "PDFCraftLauncher.exe")
Write-Host "Place the exe in the PDF Craft project directory, then double-click it to start the web app."
