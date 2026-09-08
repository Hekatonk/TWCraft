<#
.SYNOPSIS
    Generates JER world-gen.json end to end: installs a throwaway server, syncs
    the pack to it, pregenerates every dimension with Chunky, scans the region
    files with RegionScanner, and drops the result into the pack.

.DESCRIPTION
    Just Enough Resources ships vanilla ore distributions only. Modded ores stay
    blank until config/world-gen.json exists. JER's own /jer_profile
    command registers but is not implemented on 1.21.1 -- it points at an
    external tool, RegionScanner, which reads region files off disk. That means
    the world has to be generated first, which is what Chunky is for.

    Doing that by hand is a long sequence of steps that has to be repeated every
    time the ore-adding mods change, so it is scripted here. Nothing this script
    creates is part of the pack: the server lives under server/, which is both
    git- and packwiz-ignored, and only the final JSON is copied in.

    Runs unattended. Expect it to take a while -- pregeneration is the slow part
    and scales with the square of the radius.

.PARAMETER Radius
    Overworld/End pregeneration radius in blocks.

.PARAMETER NetherRadius
    Nether radius. Defaults to the same value as -Radius.

    Do NOT shrink this on the grounds that the Nether is 1:8 -- that ratio is
    about travel distance between dimensions and says nothing about sample size.
    Statistical confidence comes from the number of chunks scanned, and the
    Nether holds the rarest thing worth graphing: ancient debris sits around
    2e-5 frequency, so it wants more sampling than the Overworld, not less.

.PARAMETER Dims
    Dimensions to pregenerate and scan. Add modded ones here as they arrive,
    e.g. -Dims minecraft:overworld,mypack:mining

.PARAMETER Center
    Block coordinates to generate around. Defaults to 0,0. Chunky also supports
    `chunky spawn` and `chunky worldborder` interactively if you want those
    instead; this script uses an explicit centre so runs are reproducible.

.PARAMETER EndCenter
    Origin for minecraft:the_end only, default 3000,0. The End's central island
    is a ~1000 block disc surrounded by void and the outer islands start past
    ~1024, so a scan centred on 0,0 there is mostly empty space.

.PARAMETER Shape
    square (default) or circle. Square samples more area per radius.

.PARAMETER KeepWorld
    Keep the generated world instead of deleting it. It is scratch data and runs
    to well over a GB, so it is removed by default once the scan has succeeded.

.PARAMETER CleanServer
    Also delete the server install. Off by default: keeping it means the next run
    skips re-downloading the loader and re-syncing the mods.

.EXAMPLE
    # Defaults: local packwiz serve, all three dimensions, radius 2000 each.
    # Start `packwiz serve` in the pack root first.
    .\build-jer-worldgen.ps1

    # One dimension; merges into whatever is already shipped
    .\build-jer-worldgen.ps1 -Dims minecraft:the_nether

    # Against the published pack instead of localhost
    .\build-jer-worldgen.ps1 -PackUrl https://raw.githubusercontent.com/Hekatonk/TWCraft/main/pack.toml
#>
[CmdletBinding()]
param(
    [string]$PackUrl = "http://localhost:8080/pack.toml",
    [int]$Radius = 2000,
    [int]$NetherRadius = 0,
    [string[]]$Dims = @("minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"),
    [int[]]$Center = @(0, 0),
    [int[]]$EndCenter = @(3000, 0),
    [ValidateSet("square", "circle")]
    [string]$Shape = "square",
    [string]$Memory = "6G",
    [string]$JavaPath = "",
    [switch]$KeepWorld,
    [switch]$CleanServer,
    [switch]$SkipPregen,
    [switch]$SkipFilter
)

$ErrorActionPreference = "Stop"

<#
    Minecraft 1.21.1 needs Java 21, and `java` on PATH here is 17 -- the server
    would die with UnsupportedClassVersionError. ATLauncher already downloads a
    21 for the instance (component java-runtime-delta), so prefer that over PATH
    rather than making the user install another JDK. Override with -JavaPath.
#>
function Get-JavaMajor {
    <#
        Read the version from the JDK's own `release` file rather than running
        `java -version`. On Windows PowerShell 5.1, `2>&1` on a native exe wraps
        stderr in a NativeCommandError, and java prints its banner to stderr --
        so with $ErrorActionPreference='Stop' the probe throws instead of
        returning a version. Reading the file sidesteps that entirely and is
        faster. Falls back to executing java only for a bare "java" on PATH.
    #>
    param([string]$JavaExe)
    # NB: not $home -- that is a PowerShell automatic variable.
    $javaHome = Split-Path (Split-Path $JavaExe -Parent) -Parent   # <root>/bin/java.exe
    $release = Join-Path $javaHome "release"
    if (Test-Path $release) {
        $line = Select-String -Path $release -Pattern '^JAVA_VERSION="([^"]+)"' `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($line) {
            $v = $line.Matches[0].Groups[1].Value
            if ($v -match '^(\d+)') { return [int]$Matches[1] }
        }
    }
    # PATH fallback: contain the native-stderr behaviour to this one call.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = (& $JavaExe -version 2>&1 | Out-String)
    } catch {
        return 0
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($out -match 'version "(\d+)') { return [int]$Matches[1] }
    return 0
}

function Resolve-Java {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "-JavaPath '$Explicit' does not exist" }
        return $Explicit
    }
    $candidates = @()
    foreach ($root in @("$env:APPDATA\ATLauncher\runtimes", "D:\Minecraft\ATLauncher\runtimes",
                        "C:\Minecraft\ATLauncher\runtimes", "$env:LOCALAPPDATA\Programs\Eclipse Adoptium",
                        "C:\Program Files\Eclipse Adoptium", "C:\Program Files\Java",
                        "$env:APPDATA\.minecraft\runtime")) {
        if (Test-Path $root) {
            $candidates += Get-ChildItem -Path $root -Recurse -Filter java.exe `
                -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
        }
    }
    $candidates += "java"

    $seen = @()
    foreach ($c in $candidates) {
        $maj = Get-JavaMajor -JavaExe $c
        if ($maj -gt 0) { $seen += "$maj at $c" }
        if ($maj -ge 21) { return $c }
    }
    $found = if ($seen) { "`nJava versions found:`n  " + ($seen -join "`n  ") } else { "`nNo Java found at all." }
    throw "No Java 21+ found, which Minecraft $mcVersion requires.$found`nPass -JavaPath 'C:\path\to\java.exe'."
}
$packRoot  = (Resolve-Path "$PSScriptRoot\..").Path
$serverDir = Join-Path $packRoot "server\jer-worldgen"
$scanner   = Join-Path $packRoot "tools\region_scanner.exe"
$bootstrap = Join-Path $PSScriptRoot "packwiz-installer-bootstrap.jar"

function Say($msg) { Write-Host "[jer] $msg" -ForegroundColor Cyan }

# Read the loader and MC version straight from pack.toml so this can never drift
# from what the pack actually targets.
$packToml = Get-Content (Join-Path $packRoot "pack.toml") -Raw
$mcVersion = [regex]::Match($packToml, 'minecraft = "([^"]+)"').Groups[1].Value
$neoVersion = [regex]::Match($packToml, 'neoforge = "([^"]+)"').Groups[1].Value
if (-not $mcVersion -or -not $neoVersion) { throw "Could not read versions from pack.toml" }
Say "pack targets Minecraft $mcVersion / NeoForge $neoVersion"

if ($NetherRadius -le 0) { $NetherRadius = $Radius }

<#
    Fail early and clearly if the pack URL is unreachable. The default points at
    a local `packwiz serve`, which is easy to forget to start -- without this the
    script installs an entire server first and only then dies inside
    packwiz-installer with a much less obvious error.
#>
try {
    $probe = Invoke-WebRequest -Uri $PackUrl -UseBasicParsing -TimeoutSec 10
    Say "pack url reachable (HTTP $($probe.StatusCode))"
} catch {
    $hint = ""
    if ($PackUrl -match "localhost|127\.0\.0\.1") {
        $hint = "`n  Is 'packwiz serve' running in $packRoot ?"
    }
    throw "Cannot reach $PackUrl$hint"
}

$java = Resolve-Java -Explicit $JavaPath
Say "using java $(Get-JavaMajor -JavaExe $java): $java"

if (-not (Test-Path $scanner)) {
    throw "RegionScanner missing at $scanner. Download the x86_64-pc-windows-msvc build from https://github.com/RundownRhino/RegionScanner/releases and extract region_scanner.exe there."
}

# ---------------------------------------------------------------- server setup
New-Item -ItemType Directory -Path $serverDir -Force | Out-Null

$argsFile = Join-Path $serverDir "libraries\net\neoforged\neoforge\$neoVersion\win_args.txt"
if (-not (Test-Path $argsFile)) {
    $installer = Join-Path $serverDir "neoforge-installer.jar"
    if (-not (Test-Path $installer)) {
        Say "downloading NeoForge $neoVersion installer"
        Invoke-WebRequest -Uri "https://maven.neoforged.net/releases/net/neoforged/neoforge/$neoVersion/neoforge-$neoVersion-installer.jar" -OutFile $installer
    }
    <#
        Run from inside the server directory. The NeoForge installer writes a
        ~500 KB neoforge-installer.jar.log into its *working* directory, and the
        script is normally invoked from the pack root -- so without this it
        litters the pack with a log file. It is covered by *.log in both ignore
        files, but keeping it out of the pack root entirely is tidier.
    #>
    Say "installing server (this pulls the loader libraries; takes a minute)"
    Push-Location $serverDir
    try {
        & $java -jar $installer --installServer $serverDir | Out-Null
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) { throw "NeoForge installer failed with exit code $LASTEXITCODE" }
}
if (-not (Test-Path $argsFile)) { throw "Server install did not produce $argsFile" }

Set-Content -Path (Join-Path $serverDir "eula.txt") -Value "eula=true" -Encoding ascii

<#
    max-tick-time=-1 matters: pregeneration deliberately hammers the main thread,
    and the vanilla watchdog will otherwise decide the server has hung and kill
    it mid-run. Everything else here just keeps the throwaway server cheap.
#>
$props = @(
    "eula=true"
    "max-tick-time=-1"
    "online-mode=false"
    "view-distance=6"
    "simulation-distance=4"
    "spawn-protection=0"
    "sync-chunk-writes=false"
    "level-name=world"
) -join "`n"
Set-Content -Path (Join-Path $serverDir "server.properties") -Value $props -Encoding ascii

# ------------------------------------------------------------------ pack sync
Say "syncing pack to the server (side=server) from $PackUrl"
& $java -jar $bootstrap -g -s server --pack-folder $serverDir $PackUrl
if ($LASTEXITCODE -ne 0) { throw "packwiz-installer failed with exit code $LASTEXITCODE" }

Set-Content -Path (Join-Path $serverDir "user_jvm_args.txt") -Value "-Xmx$Memory" -Encoding ascii

if ($SkipPregen) {
    Say "-SkipPregen set, going straight to scanning"
} else {
    # ------------------------------------------------------------- run + pregen
    <#
        The server is driven through its stdin/stdout rather than run.bat, so the
        script can issue Chunky commands when the server reports ready and stop it
        the moment the last dimension finishes. Chunky prints
        "Task finished for <world>." per dimension -- that is the completion signal.
    #>
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $java
    $psi.Arguments = "@user_jvm_args.txt @`"$argsFile`" nogui"
    $psi.WorkingDirectory = $serverDir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    Say "starting server; pregenerating $($Dims -join ', ')"
    $proc = [System.Diagnostics.Process]::Start($psi)

    $pending = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($d in $Dims) { [void]$pending.Add($d) }
    $queued = $false

    while (-not $proc.HasExited) {
        $line = $proc.StandardOutput.ReadLine()
        if ($null -eq $line) { break }
        Write-Host "  | $line"

        # "Done (12.345s)! For help, type "help"" -- server is accepting commands.
        if (-not $queued -and $line -match 'Done \([\d.]+s\)!') {
            $queued = $true
            foreach ($d in $Dims) {
                $r = if ($d -eq "minecraft:the_nether") { $NetherRadius } else { $Radius }
                <#
                    The End's central island is a ~1000 block disc ringed by
                    void, and the outer islands only begin past ~1024. Centring
                    a scan on 0,0 there samples mostly empty space, so the End
                    gets its own origin out among the outer islands.

                    Note the Nether gets no such special case for radius: see
                    the -NetherRadius help. Sample size is chunks scanned, not
                    overworld-equivalent distance.
                #>
                $c = switch ($d) {
                    "minecraft:the_end"    { $EndCenter }
                    default                { $Center }
                }
                # ${d} braces required: "$d:" would parse as a drive-qualified variable.
                Say "queueing ${d} - $Shape radius $r centered $($c[0]),$($c[1])"
                $proc.StandardInput.WriteLine("chunky world $d")
                $proc.StandardInput.WriteLine("chunky center $($c[0]) $($c[1])")
                $proc.StandardInput.WriteLine("chunky shape $Shape")
                $proc.StandardInput.WriteLine("chunky radius $r")
                $proc.StandardInput.WriteLine("chunky start")
                $proc.StandardInput.Flush()
                Start-Sleep -Milliseconds 500
            }
        }

        if ($line -match 'Task finished for ([^.]+)\.') {
            $done = $Matches[1].Trim()
            $hit = @($pending) | Where-Object { $_ -eq $done -or $done -like "*$($_.Split(':')[-1])*" }
            foreach ($h in $hit) { [void]$pending.Remove($h) }
            Say "finished $done ($($pending.Count) dimension(s) left)"
            if ($pending.Count -eq 0) {
                Say "all dimensions done, stopping server"
                $proc.StandardInput.WriteLine("stop")
                $proc.StandardInput.Flush()
            }
        }
    }
    $proc.WaitForExit()
    Say "server exited with code $($proc.ExitCode)"
}

# ---------------------------------------------------------------------- scan
$world = Join-Path $serverDir "world"
if (-not (Test-Path $world)) { throw "No world folder at $world -- did pregeneration run?" }

$outDir = Join-Path $serverDir "scanner-out"
Say "scanning region files"
& $scanner --path $world --dims $Dims --output $outDir
if ($LASTEXITCODE -ne 0) {
    Write-Warning "RegionScanner exited $LASTEXITCODE. If it reported zero scannable chunks, retry with --proto include."
    throw "RegionScanner failed"
}

$generated = Join-Path $outDir "world-gen.json"
if (-not (Test-Path $generated)) { throw "RegionScanner produced no world-gen.json in $outDir" }

<#
    RegionScanner counts every block it sees, so a raw scan is ~75% terrain and
    scenery -- air, stone, water, leaves, and mineshaft furniture like rails and
    spawners. Shipping that would give JER a distribution page for dirt. Filter
    down to actual resources before the file enters the pack. (RegionScanner's
    own --only-blocks-above is no use here: it filters by frequency, so it drops
    rare ores long before it drops dirt.)
#>
$packWorldGen = Join-Path $packRoot "config\world-gen.json"
if (-not $SkipFilter) {
    <#
        Merge rather than overwrite. JER treats this file as a total replacement
        for its built-in data -- Compatibility.init() skips registering vanilla
        worldgen entirely once the file exists -- so a dimension absent from the
        file gets no graphs at all, not even vanilla's. Writing a Nether-only
        scan over an Overworld file would silently delete every Overworld ore
        page. Merging per dimension keeps earlier scans intact.
    #>
    Say "filtering terrain out of the scan, merging with any existing data"
    & python (Join-Path $packRoot "scripts\filter-worldgen.py") $generated --merge-into $packWorldGen
    if ($LASTEXITCODE -ne 0) { throw "filter-worldgen.py failed with exit code $LASTEXITCODE" }
}

<#
    JER reads FMLPaths.CONFIGDIR.resolve("world-gen.json") -- the config/ root,
    NOT config/jeresources/. Putting it in a jeresources subfolder looks tidier
    and silently does nothing: the file loads without error and no ore graphs
    appear. Verified by disassembling WorldGenAdapter.getWorldGenFile().
#>
$dest = Join-Path $packRoot "config"
New-Item -ItemType Directory -Path $dest -Force | Out-Null
Copy-Item $generated (Join-Path $dest "world-gen.json") -Force
Say "wrote config/world-gen.json"

& packwiz refresh
Say "done -- review the diff, then commit config/world-gen.json"

<#
    Cleanup. The generated world is by far the biggest thing here -- a single
    Overworld pass at the default radius runs well over a GB -- and it is pure
    scratch: the scan has already been reduced to a ~30 KB JSON, and re-running
    this script regenerates it from scratch anyway. Delete it by default.

    Deliberately after the scan succeeded: if RegionScanner had failed, the run
    throws above and the world survives, so a retry does not have to pregenerate
    all over again.

    The server install itself is kept -- it is the loader libraries and synced
    mods, expensive to fetch and cheap to keep -- unless -CleanServer says
    otherwise.
#>
function Get-DirSizeMB($path) {
    if (-not (Test-Path $path)) { return 0 }
    $bytes = (Get-ChildItem $path -Recurse -File -Force -ErrorAction SilentlyContinue |
              Measure-Object -Property Length -Sum).Sum
    return [math]::Round(($bytes / 1MB), 1)
}

if ($CleanServer) {
    $mb = Get-DirSizeMB $serverDir
    Say "removing the whole server ($mb MB)"
    Remove-Item $serverDir -Recurse -Force
} elseif (-not $KeepWorld) {
    $mb = Get-DirSizeMB $world
    Say "removing the generated world ($mb MB); pass -KeepWorld to keep it"
    Remove-Item $world -Recurse -Force
    # The scan output is already copied into the pack; no reason to keep a
    # second copy around either.
    if (Test-Path $outDir) { Remove-Item $outDir -Recurse -Force }
    Say "server install kept at $serverDir so the next run skips the download"
} else {
    Say "world kept at $world ($(Get-DirSizeMB $world) MB)"
}
