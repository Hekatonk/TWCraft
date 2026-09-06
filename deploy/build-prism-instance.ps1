<#
.SYNOPSIS
    Builds a Prism/MultiMC instance zip for TWCraft.

.DESCRIPTION
    The zip carries the loader version and the packwiz pre-launch command, and
    ships an EMPTY mods folder on purpose. packwiz-installer downloads every mod
    itself on first launch, so it owns every file it later needs to remove.

    That is the whole point of distributing this way rather than a CurseForge
    zip: when the launcher installs the mods, packwiz has no record of them and
    can never delete them, so removing a mod from the pack leaves it behind on
    every client forever.

.PARAMETER PackUrl
    URL of pack.toml. Use the local packwiz serve address to test, and the
    published URL to distribute.

.EXAMPLE
    .\build-prism-instance.ps1 -PackUrl http://localhost:8080/pack.toml
    .\build-prism-instance.ps1 -PackUrl https://raw.githubusercontent.com/Hekatonk/TWCraft/main/pack.toml
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackUrl,

    [string]$InstanceName = "TWCraft",

    [string]$OutputDir = "$PSScriptRoot\out"
)

$ErrorActionPreference = "Stop"

<#
    Writes UTF-8 with NO byte order mark.

    Windows PowerShell 5.1's `-Encoding utf8` means utf8-WITH-BOM, and there is no
    flag to turn that off -- the .NET encoder is the only way. It matters here:
    instance.cfg is parsed by Prism as plain key=value lines, and a BOM makes the
    first key read as "<U+FEFF>InstanceType" rather than "InstanceType".

    The same mistake in a packwiz .pw.toml file is fatal rather than cosmetic --
    packwiz's TOML parser rejects a leading BOM outright.
#>
function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

$bootstrapUrl = "https://github.com/packwiz/packwiz-installer-bootstrap/releases/latest/download/packwiz-installer-bootstrap.jar"
$cachedBootstrap = "$PSScriptRoot\packwiz-installer-bootstrap.jar"
$templatePack = "$PSScriptRoot\template\mmc-pack.json"
$staging = "$OutputDir\$InstanceName"
$zipPath = "$OutputDir\$InstanceName.zip"

# Cache the bootstrap jar rather than re-downloading per build.
if (-not (Test-Path $cachedBootstrap)) {
    Write-Host "Downloading packwiz-installer-bootstrap..."
    Invoke-WebRequest -Uri $bootstrapUrl -OutFile $cachedBootstrap
}

if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path "$staging\.minecraft\mods" -Force | Out-Null

Copy-Item $templatePack "$staging\mmc-pack.json"
Copy-Item $cachedBootstrap "$staging\.minecraft\packwiz-installer-bootstrap.jar"

# The pre-launch command runs with .minecraft as the working directory, which is
# why the bootstrap jar is referenced by bare filename. $INST_JAVA is substituted
# by Prism with the instance's own Java, so this does not depend on a system JDK.
# For 1.21.1 that Java is 21 -- Prism picks it from the instance's Java setting,
# so the bootstrap and the game always agree on a runtime.
$instanceCfg = @"
InstanceType=OneSix
name=$InstanceName
OverrideCommands=true
PreLaunchCommand="`$INST_JAVA" -jar packwiz-installer-bootstrap.jar $PackUrl
"@
Write-Utf8NoBom "$staging\instance.cfg" $instanceCfg

# An empty mods/ does not survive a zip, so leave a marker explaining why it is
# empty -- otherwise the first thing anyone does is assume the zip is broken.
Write-Utf8NoBom "$staging\.minecraft\mods\README.txt" @"
This folder is intentionally empty.

Mods are downloaded by packwiz-installer when the instance first launches, so
that packwiz tracks every file it installs and can remove them again when they
leave the pack. Do not add mods here by hand -- they will not be tracked, and
they will not be removed when the pack changes.
"@

if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

# Built by hand rather than with Compress-Archive: on Windows PowerShell 5.1 that
# cmdlet writes entry names with backslashes, which the ZIP spec forbids (4.4.17.1
# requires forward slashes). Some extractors silently cope and others produce a
# single file literally named ".minecraft\mods\README.txt".
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $stagingRoot = (Resolve-Path $staging).Path
    foreach ($file in Get-ChildItem $staging -Recurse -File -Force) {
        $relative = $file.FullName.Substring($stagingRoot.Length + 1).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $file.FullName, $relative) | Out-Null
    }
} finally {
    $archive.Dispose()
}

# Reported from the template rather than hardcoded, so bumping the loader in
# mmc-pack.json cannot leave this line quietly lying about what was built.
$components = (Get-Content $templatePack -Raw | ConvertFrom-Json).components
$mc = ($components | Where-Object { $_.uid -eq "net.minecraft" }).version
$loader = ($components | Where-Object { $_.uid -eq "net.neoforged" }).version

Write-Host ""
Write-Host "Built $zipPath"
Write-Host "  pack url : $PackUrl"
Write-Host "  loader   : NeoForge $loader / Minecraft $mc (from template/mmc-pack.json)"
Write-Host ""
Write-Host "Import in Prism with: Add Instance -> Import from zip"
