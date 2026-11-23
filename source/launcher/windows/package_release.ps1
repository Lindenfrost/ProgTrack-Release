param(
    [string]$Version = "0.3.0",
    [string]$Commit = "HEAD",
    [string]$RuntimeDirectory = (Join-Path $PSScriptRoot "dist\ProgTrack_small"),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "release")
)

$ErrorActionPreference = "Stop"

$repository = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$runtime = (Resolve-Path $RuntimeDirectory).Path
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$releaseName = "ProgTrack-$Version"
$stagingRoot = [IO.Path]::GetFullPath((Join-Path $outputRoot "staging"))
$packageRoot = [IO.Path]::GetFullPath((Join-Path $stagingRoot $releaseName))
$payloadArchive = [IO.Path]::GetFullPath((Join-Path $outputRoot "payload.zip"))
$releaseArchive = [IO.Path]::GetFullPath((Join-Path $outputRoot "$releaseName.zip"))
$checksumPath = "$releaseArchive.sha256"

function Assert-ChildPath {
    param([string]$Path, [string]$Parent)
    $normalizedParent = [IO.Path]::GetFullPath($Parent).TrimEnd("\") + "\"
    $normalizedPath = [IO.Path]::GetFullPath($Path)
    if (-not $normalizedPath.StartsWith($normalizedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing filesystem operation outside $normalizedParent`: $normalizedPath"
    }
}

function Remove-GeneratedPath {
    param([string]$Path)
    Assert-ChildPath -Path $Path -Parent $outputRoot
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

foreach ($required in @(
    (Join-Path $runtime "Launcher.exe"),
    (Join-Path $runtime "_internal"),
    (Join-Path $runtime "_internal\_ctypes.pyd"),
    (Join-Path $runtime "_internal\_multiprocessing.pyd"),
    (Join-Path $runtime "_internal\_sqlite3.pyd"),
    (Join-Path $runtime "_internal\PyQt6\QtCore.pyd")
)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required frozen runtime component is missing: $required"
    }
}

$pythonRuntimeDlls = @(
    Get-ChildItem -LiteralPath (Join-Path $runtime "_internal") -File -Filter "python*.dll" |
        Where-Object { $_.Name -match "^python(?<abi>\d{3})\.dll$" }
)
if ($pythonRuntimeDlls.Count -ne 1) {
    throw "Expected exactly one versioned Python runtime DLL; found $($pythonRuntimeDlls.Count)."
}
$runtimeAbi = [regex]::Match($pythonRuntimeDlls[0].Name, "\d{3}").Value

$pydFiles = @(Get-ChildItem -LiteralPath (Join-Path $runtime "_internal") -Recurse -File -Filter "*.pyd")
if ($pydFiles.Count -lt 50) {
    throw "Frozen runtime contains only $($pydFiles.Count) .pyd files; native modules appear incomplete."
}
$extensionAbis = @(
    $pydFiles |
        ForEach-Object {
            $match = [regex]::Match($_.Name, "\.cp(?<abi>\d{3})-")
            if ($match.Success) { $match.Groups["abi"].Value }
        } |
        Sort-Object -Unique
)
if ($extensionAbis.Count -ne 1 -or $extensionAbis[0] -ne $runtimeAbi) {
    throw "Python extension ABI set '$($extensionAbis -join ",")' does not match runtime ABI '$runtimeAbi'."
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
Remove-GeneratedPath -Path $stagingRoot
Remove-GeneratedPath -Path $payloadArchive
Remove-GeneratedPath -Path $releaseArchive
if (Test-Path -LiteralPath $checksumPath) {
    Assert-ChildPath -Path $checksumPath -Parent $outputRoot
    Remove-Item -LiteralPath $checksumPath -Force
}
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

$resolvedCommit = (& git -C $repository rev-parse --verify "$Commit^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or -not $resolvedCommit) {
    throw "Cannot resolve Git commit: $Commit"
}

$payloadPaths = @(
    "ProgTrack.v.$Version.py",
    "Plugins",
    "Resources",
    "icons",
    "lang",
    "manual/LICENSE_NOTICE.md",
    "manual/ProgTrack_User_Guide - de.html",
    "manual/ProgTrack_User_Guide - en.html",
    "manual/ProgTrack_User_Guide - it.html",
    "manual/ProgTrack_User_Guide - ru.html",
    "third_party_licenses",
    "info.json",
    "info_de.json",
    "info_en.json",
    "info_it.json",
    "info_ru.json",
    "README.md",
    "LICENSE",
    "LICENSE_NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "release_inventory.json",
    "Username + 123456 password.png"
)

& git -C $repository archive --format=zip "--output=$payloadArchive" $resolvedCommit -- $payloadPaths
if ($LASTEXITCODE -ne 0) {
    throw "git archive failed"
}
Expand-Archive -LiteralPath $payloadArchive -DestinationPath $packageRoot -Force

Copy-Item -LiteralPath (Join-Path $runtime "Launcher.exe") -Destination $packageRoot
Copy-Item -LiteralPath (Join-Path $runtime "_internal") -Destination $packageRoot -Recurse
foreach ($excludedRuntimePath in @(
    (Join-Path $packageRoot "_internal\logs"),
    (Join-Path $packageRoot "_internal\matplotlib_cache"),
    (Join-Path $packageRoot "_internal\PyQt6\Qt6\plugins\multimedia\ffmpegmediaplugin.dll")
)) {
    Remove-GeneratedPath -Path $excludedRuntimePath
}

$inventory = Get-Content -Raw -LiteralPath (Join-Path $runtime "component_inventory.json") | ConvertFrom-Json
if (-not $inventory.valid -or $inventory.psycopg_implementation -ne "binary" -or
    @($inventory.python_runtime_families).Count -ne 1 -or $inventory.python_runtime_families[0] -ne $runtimeAbi) {
    throw "Frozen runtime inventory validation failed."
}
$frozenFiles = @{}
foreach ($entry in $inventory.files) {
    $frozenFiles.Add($entry.path, $entry)
}
$inventory.files = @(
    Get-ChildItem -LiteralPath $packageRoot -Recurse -File -Force | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($packageRoot.Length + 1).Replace("\", "/")
        $fileHash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $entry = [ordered]@{path = $relative; bytes = $_.Length; sha256 = $fileHash}
        if ($relative -eq "Launcher.exe" -or $relative.StartsWith("_internal/")) {
            $expected = $frozenFiles[$relative]
            if ($null -eq $expected -or $_.Length -ne $expected.bytes -or $fileHash -ne $expected.sha256) {
                throw "Staged runtime differs from frozen build inventory: $relative"
            }
            $frozenFiles.Remove($relative)
        }
        if ($_.Extension -in ".dll", ".pyd", ".exe") {
            $entry.native = @{
                file_version = $_.VersionInfo.FileVersion
                product = $_.VersionInfo.ProductName
                company = $_.VersionInfo.CompanyName
            }
            if ($_.Name -match "\.(cp\d{3})-" -or $_.Name -match "\.(abi3)\.pyd$") {
                $entry.native.python_abi = $Matches[1]
            }
        }
        [pscustomobject]$entry
    }
)
foreach ($missing in $frozenFiles.Keys) {
    if ($missing -notmatch "^_internal/(logs/|matplotlib_cache/|PyQt6/Qt6/plugins/multimedia/ffmpegmediaplugin\.dll$)") {
        throw "Staged runtime lost a frozen component: $missing"
    }
}
$inventory.schema = "progtrack-component-inventory/2"
$inventory | Add-Member -NotePropertyName source_commit -NotePropertyValue $resolvedCommit
$inventory | Add-Member -NotePropertyName platform -NotePropertyValue "windows-x86_64"
[IO.File]::WriteAllText(
    (Join-Path $packageRoot "component_inventory.json"),
    (($inventory | ConvertTo-Json -Depth 8) + "`n"),
    [Text.UTF8Encoding]::new($false)
)

if (-not (Test-Path -LiteralPath (Join-Path $packageRoot "Resources\Seed\progtrack_seed.ptdb"))) {
    throw "The committed Phase 2 sample-data seed is missing from the release package."
}

& tar -a -c -f $releaseArchive -C $stagingRoot $releaseName
if ($LASTEXITCODE -ne 0) {
    throw "Release archive creation failed"
}

$archiveEntries = @(& tar -tf $releaseArchive)
if ($LASTEXITCODE -ne 0) {
    throw "Cannot inspect release archive"
}
$archivedPydCount = @($archiveEntries | Where-Object { $_ -like "*.pyd" }).Count
if ($archivedPydCount -ne $pydFiles.Count) {
    throw "Archive contains $archivedPydCount .pyd files; expected $($pydFiles.Count)."
}
foreach ($requiredEntry in @(
    "$releaseName/_internal/_ctypes.pyd",
    "$releaseName/_internal/_multiprocessing.pyd",
    "$releaseName/_internal/_sqlite3.pyd",
    "$releaseName/_internal/PyQt6/QtCore.pyd",
    "$releaseName/Resources/Seed/progtrack_seed.ptdb",
    "$releaseName/release_inventory.json",
    "$releaseName/component_inventory.json"
)) {
    if ($requiredEntry -notin $archiveEntries) {
        throw "Release archive is missing: $requiredEntry"
    }
}
if ($archiveEntries | Where-Object {
    $_ -match "^$([regex]::Escape($releaseName))/(tests|tmp|outputs|source)/" -or
    $_ -match "^$([regex]::Escape($releaseName))/_internal/(logs|matplotlib_cache)/" -or
    $_ -eq "$releaseName/_internal/PyQt6/Qt6/plugins/multimedia/ffmpegmediaplugin.dll"
}) {
    throw "Release archive contains an excluded development/runtime artifact."
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $releaseArchive).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(
    $checksumPath,
    "$hash  $releaseName.zip`n",
    [Text.UTF8Encoding]::new($false)
)

[pscustomobject]@{
    Version = $Version
    Commit = $resolvedCommit
    RuntimeAbi = "cp$runtimeAbi"
    NativeExtensionCount = $pydFiles.Count
    Archive = $releaseArchive
    ArchiveBytes = (Get-Item -LiteralPath $releaseArchive).Length
    Sha256 = $hash
    Checksum = $checksumPath
}
