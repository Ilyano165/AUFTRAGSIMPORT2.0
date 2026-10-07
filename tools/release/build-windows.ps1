<#
.SYNOPSIS
    Reproduzierbarer Windows-Build von IC-Ware Auftrags-Import: Programm (PyInstaller) und MSI (WiX).

.DESCRIPTION
    STAND: NOT TESTED ON WINDOWS. Dieses Skript wurde noch nie unter Windows ausgeführt; erst ein
    erfolgreicher Lauf auf einem Windows-Rechner belegt den lokalen Windows-Build.

    Ablauf: Windows und Architektur prüfen, Python laut .python-version prüfen, frische
    virtuelle Umgebung, Abhängigkeiten nur mit geprüften Prüfsummen (requirements\*.lock),
    Prüfungen (Lint, Format, mypy --strict, Tests), Programm, Produktprüfung, Selbsttest, MSI,
    Prüfung der MSI, Prüfsummen und Manifest. Jeder Fehler bricht sofort mit Exit-Code 1 ab.

    Testbuild (Standard): unsigniert und mit Platzhalter-Lizenz zulässig, MSI heißt
    ...-x64-TESTBUILD.msi, nur für interne Tests.
    Release (-Release): verlangt endgültigen Lizenztext, sauberen Git-Stand, CHANGELOG-Abschnitt
    und Signatur über ICW_SIGN_COMMAND (docs/release/signing.md).

    Voraussetzungen: Windows x64, Python 3.14.4 (64 Bit, python.org) mit Launcher "py",
    .NET SDK, WiX 5.0.2 mit WixToolset.UI.wixext 5.0.2 (docs/release/build.md).

    Das Skript enthält keine Zugangsdaten und gibt keine Umgebungsvariablen aus.

.PARAMETER Release
    Release-Build statt Testbuild.

.PARAMETER BuildNumber
    Numerische Build-Nummer (Standard 0).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\release\build-windows.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\release\build-windows.ps1 -Release -BuildNumber 57
#>
[CmdletBinding()]
param(
    [switch]$Release,
    [ValidatePattern('^[0-9]+$')]
    [string]$BuildNumber = '0'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ExpectedWix = '5.0.2'   # identisch mit WIX_VERSION in .github/workflows/windows-release.yml
$VenvName = '.venv-build'

function Stop-Build([string]$Message) {
    Write-Host ''
    Write-Host "ABBRUCH: $Message" -ForegroundColor Red
    exit 1
}

trap {
    Stop-Build $_.Exception.Message
}

function Write-Step([string]$Title) {
    Write-Host ''
    Write-Host "=== $Title ===" -ForegroundColor Cyan
}

function Invoke-Checked([string]$Title, [string]$File, [string[]]$Arguments) {
    Write-Step $Title
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        Stop-Build "$Title fehlgeschlagen (Exit-Code $LASTEXITCODE)"
    }
}

function Get-Output([string]$File, [string[]]$Arguments) {
    $output = & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        Stop-Build "$File $($Arguments -join ' ') fehlgeschlagen (Exit-Code $LASTEXITCODE)"
    }
    return (($output | Out-String).Trim())
}

# --- 1. Windows-Version ----------------------------------------------------------------------
if ($env:OS -ne 'Windows_NT') {
    Stop-Build 'Dieses Skript läuft nur unter Windows.'
}
Write-Step 'Windows'
$os = Get-CimInstance -ClassName Win32_OperatingSystem
Write-Host "$($os.Caption) $($os.Version) (Build $($os.BuildNumber))"

# --- 2. Architektur --------------------------------------------------------------------------
if (-not [Environment]::Is64BitOperatingSystem) {
    Stop-Build '64-Bit-Windows erforderlich.'
}
$architecture = $env:PROCESSOR_ARCHITECTURE
if ($null -ne $env:PROCESSOR_ARCHITEW6432) {
    $architecture = $env:PROCESSOR_ARCHITEW6432
}
Write-Host "Architektur: $architecture"
if ($architecture -ne 'AMD64') {
    Stop-Build "Architektur $architecture; der Build braucht x64 (AMD64)."
}

# --- Projekt und Werkzeuge -------------------------------------------------------------------
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $Root
Write-Host "Projekt: $Root"
$env:PYTHONHASHSEED = '0'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QT_QPA_FONTDIR = Join-Path $env:WINDIR 'Fonts'   # sonst keine Schriften im Offscreen-Modus

if ($null -eq (Get-Command -Name wix -ErrorAction SilentlyContinue)) {
    Stop-Build "WiX fehlt: dotnet tool install --global wix --version $ExpectedWix"
}
$wixVersion = Get-Output 'wix' @('--version')
Write-Host "WiX: $wixVersion"
if (-not $wixVersion.StartsWith($ExpectedWix)) {
    Stop-Build "WiX $wixVersion gefunden, verlangt $ExpectedWix (Abgleich mit der Build-Pipeline)."
}
$extensions = Get-Output 'wix' @('extension', 'list', '--global')
if ($extensions -notmatch ('WixToolset\.UI\.wixext.*' + [regex]::Escape($ExpectedWix))) {
    Stop-Build "WiX-UI-Erweiterung fehlt: wix extension add --global WixToolset.UI.wixext/$ExpectedWix"
}

# --- 3./4. Python ----------------------------------------------------------------------------
$wanted = (Get-Content -LiteralPath '.python-version' -Raw).Trim()
$minor = ($wanted.Split('.')[0..1]) -join '.'
if ($null -eq (Get-Command -Name py -ErrorAction SilentlyContinue)) {
    Stop-Build "Python-Launcher 'py' fehlt; Python $wanted (64 Bit) von python.org installieren."
}
$pythonExe = Get-Output 'py' @("-$minor", '-c', 'import sys; print(sys.executable)')
$pythonVersion = Get-Output $pythonExe @('-c', 'import platform; print(platform.python_version())')
$pythonBits = Get-Output $pythonExe @('-c', 'import struct; print(struct.calcsize(chr(80)) * 8)')
Write-Step 'Python'
Write-Host "Python $pythonVersion ($pythonBits Bit): $pythonExe"
if ($pythonVersion -ne $wanted) {
    Stop-Build "Python $pythonVersion gefunden, verlangt $wanted (.python-version)."
}
if ($pythonBits -ne '64') {
    Stop-Build "Python mit $pythonBits Bit; der Build braucht 64 Bit."
}

# --- Frischer Stand --------------------------------------------------------------------------
Write-Step 'Frischer Stand (build, dist, virtuelle Umgebung)'
foreach ($folder in @('build', 'dist', $VenvName)) {
    if (Test-Path -LiteralPath $folder) {
        Remove-Item -LiteralPath $folder -Recurse -Force
        Write-Host "entfernt: $folder"
    }
}

# --- 5. Virtuelle Umgebung -------------------------------------------------------------------
Invoke-Checked 'Virtuelle Umgebung' $pythonExe @('-m', 'venv', $VenvName)
$py = Join-Path $Root "$VenvName\Scripts\python.exe"

# --- 6. pip ----------------------------------------------------------------------------------
# Bewusst KEIN Update: ein ungesperrtes "pip install --upgrade pip" lädt eine beliebige Version
# von PyPI und unterläuft den Prüfsummen-Prozess. Verwendet wird das pip von Python 3.14.4.
Invoke-Checked 'pip (mitgeliefert, nicht aktualisiert)' $py @('-m', 'pip', '--version')

# --- 7. Abhängigkeiten nur mit geprüften Prüfsummen -------------------------------------------
$pipInstall = @('-m', 'pip', 'install', '--require-hashes', '--no-deps', '--only-binary=:all:', '-r')
Invoke-Checked 'Build-Abhängigkeiten (requirements\build-windows.lock)' $py ($pipInstall + 'requirements\build-windows.lock')
Invoke-Checked 'Prüfwerkzeuge (requirements\ci-tools.lock)' $py ($pipInstall + 'requirements\ci-tools.lock')
Invoke-Checked 'Projekt einbinden' $py @('-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', '.')

# --- 8. Build-Prüfungen ----------------------------------------------------------------------
$build = 'tools\release\build.py'
Invoke-Checked 'Build-Umgebung' $py @($build, 'check-env')
Invoke-Checked 'Prüfungen (Lint, Format, mypy --strict, Testsuite)' $py @($build, 'test')

# --- 9. Windows-Build ------------------------------------------------------------------------
$prepare = @($build, 'prepare', '--build-number', $BuildNumber)
$releaseFlag = @()
if ($Release) {
    $prepare += '--release'
    $releaseFlag = @('--release')
}
Invoke-Checked 'Vorbereiten' $py $prepare
Invoke-Checked 'Programm erzeugen (PyInstaller)' $py @($build, 'freeze')
Invoke-Checked 'Produktprüfung' $py @($build, 'audit')
Invoke-Checked 'Selbsttest der fertigen EXE' $py @($build, 'selftest')
if ($Release) {
    Invoke-Checked 'EXE signieren' $py (@($build, 'sign-exe') + $releaseFlag)
}
Invoke-Checked 'Installer erzeugen (WiX)' $py @($build, 'msi')
if ($Release) {
    Invoke-Checked 'MSI signieren' $py (@($build, 'sign-msi') + $releaseFlag)
}

# --- 10. MSI vorhanden und geprüft -----------------------------------------------------------
Invoke-Checked 'Installer prüfen' $py @($build, 'verify')
Invoke-Checked 'Prüfsummen und Manifest' $py @($build, 'manifest')

$msiFiles = @(Get-ChildItem -LiteralPath 'dist' -Filter '*.msi' -File)
if ($msiFiles.Count -ne 1) {
    Stop-Build "Erwartet genau eine MSI in dist, gefunden: $($msiFiles.Count)"
}
$msi = $msiFiles[0]

# --- 11.-13. Pfad, SHA-256, Version ----------------------------------------------------------
$hash = (Get-FileHash -LiteralPath $msi.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$sums = Get-Content -LiteralPath 'dist\SHA256SUMS.txt' -Raw
if ($sums -notmatch [regex]::Escape("$hash  $($msi.Name)")) {
    Stop-Build 'SHA-256 der MSI passt nicht zu dist\SHA256SUMS.txt.'
}
$manifest = Get-Content -LiteralPath 'dist\build-manifest.json' -Raw | ConvertFrom-Json

Write-Step 'Ergebnis'
Write-Host "MSI:       $($msi.FullName)"
Write-Host "Größe:     $($msi.Length) Byte"
Write-Host "SHA-256:   $hash"
Write-Host "Version:   $($manifest.version) (MSI-Version $($manifest.msi_version))"
Write-Host "Build:     $((Get-Content -LiteralPath 'build\version.txt' -Raw).Trim())"
Write-Host "Build-Art: $($manifest.build_type)"
if ($manifest.build_type -eq 'test') {
    Write-Host 'TESTBUILD: unsigniert, Platzhalter-Lizenz – nur für interne Tests, nicht an Kunden.' -ForegroundColor Yellow
}
exit 0
