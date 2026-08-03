param(
    [string]$RepoRoot = "Q:\GitHub\ProgTrack-Release",
    [string]$OutputPath = "",
    [string]$DiagnosticLog = ""
)

$ErrorActionPreference = "Stop"
$schemaVersion = "phase2a-audit-evidence/1"
$scriptVersion = "1.0.1"

if (-not $OutputPath) {
    $OutputPath = Join-Path $PSScriptRoot "phase2a_audit_result.json"
}

$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path.TrimEnd("\")
$LegacyArchiveRoot = Join-Path (Split-Path -Parent $RepoRoot) "Archive\ProgTrack-legacy-json"
if (-not (Test-Path -LiteralPath $LegacyArchiveRoot)) {
    throw "Legacy JSON archive is required for this historical audit: $LegacyArchiveRoot"
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if ($OutputPath.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Audit output must be outside the ProgTrack repository."
}

function Rel([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        return $resolved
    }
    return $resolved.Substring($RepoRoot.Length).TrimStart("\").Replace("\", "/")
}

function Git([string[]]$Arguments) {
    $result = & git.exe -C $RepoRoot @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $result"
    }
    return @($result)
}

function Read-Json([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
}

function Stage([string]$Message) {
    $entry = "[{0:HH:mm:ss}] {1}" -f (Get-Date), $Message
    Write-Output $entry
    if ($DiagnosticLog) {
        Add-Content -LiteralPath $DiagnosticLog -Value $entry -Encoding utf8
    }
}

function Sha([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ObjectPropertyNames($Object) {
    if ($null -eq $Object) { return @() }
    return @($Object.PSObject.Properties | ForEach-Object { $_.Name })
}

function Classify-Path([string]$RelativePath) {
    $p = $RelativePath.Replace("\", "/")
    switch -Regex ($p) {
        "^progtrack_daten\.json$" { return "authoritative_mixed_core" }
        "^progtrack_(daten\.lock|settings\.json)" { return "core_runtime_or_configuration" }
        "^disabled_plugins\.json$" { return "installation_or_user_configuration" }
        "^Plugins/core/" { return "shared_configuration" }
        "^Plugins/Resources/" { return "packaged_or_controlled_resource" }
        "^Resources/ExampleFiles/" { return "packaged_import_template" }
        "^Plugins/Master_Track/sessions/" { return "session_or_user_state" }
        "^Plugins/Master_Track/audit_" { return "legacy_audit_excluded" }
        "^Plugins/Master_Track/(users\.enc|jobs\.json|settings\.json)" { return "security_or_configuration" }
        "^Plugins/Medi_Track/medi_track/" { return "managed_document_payload" }
        "^Plugins/Projects_Track/(documents|sop)/" { return "managed_document_payload" }
        "project_assignment_cache|projects_cache\.json" { return "rebuildable_cache_or_user_state" }
        "\.(bak|tmp|lock)$" { return "transient_or_compatibility" }
        "\.(png|pdf|xlsx|xls|csv)$" { return "resource_managed_payload_or_output_review_required" }
        "\.(json|txt|enc|log)$" { return "data_or_configuration_review_required" }
        default { return "source_or_other" }
    }
}

$statusBefore = @(Git @("status", "--porcelain=v1", "--untracked-files=all"))
$branch = @(Git @("rev-parse", "--abbrev-ref", "HEAD"))[0]
$commit = @(Git @("rev-parse", "HEAD"))[0]
$trackedCount = @(Git @("ls-files")).Count
$repoFiles = @(Git @("ls-files", "--cached") |
    Sort-Object -Unique |
    Where-Object { $_.Replace("\", "/") -notmatch "(^|/)_internal/" } |
    ForEach-Object { Join-Path $RepoRoot $_ } |
    ForEach-Object { Get-Item -LiteralPath $_ -ErrorAction SilentlyContinue })
Stage "Repository scope collected: $($repoFiles.Count) files (excluding _internal)."

$jsonFailures = @()
$jsonFiles = @($repoFiles | Where-Object {
    $_.Extension -eq ".json" -and
    -not ((Rel $_.FullName) -match "^info(?:_[a-z]{2})?\.json$")
})
foreach ($file in $jsonFiles) {
    try { $null = Read-Json $file.FullName }
    catch {
        $jsonFailures += [pscustomobject]@{
            path = Rel $file.FullName
            error = $_.Exception.Message
        }
    }
}
Stage "JSON parsing completed."

$manifests = @()
$manifestFailures = @()
$manifestFiles = @($repoFiles | Where-Object {
    $_.Name -eq "manifest.json" -and (Rel $_.FullName) -match "^Plugins/"
})
foreach ($file in $manifestFiles) {
    try {
        $manifest = Read-Json $file.FullName
        $entry = [string]$manifest.entry_point
        $moduleName = if ($entry.Contains(".")) { $entry.Split(".")[0] } else { $entry }
        $modulePath = Join-Path $file.DirectoryName ($moduleName + ".py")
        $manifests += [pscustomobject]@{
            path = Rel $file.FullName
            name = [string]$manifest.name
            entry_point = $entry
            entry_module_path = Rel $modulePath
            entry_module_exists = Test-Path -LiteralPath $modulePath
            declared_data_files = @($manifest.data_files)
        }
    }
    catch {
        $manifestFailures += [pscustomobject]@{
            path = Rel $file.FullName
            error = $_.Exception.Message
        }
    }
}
Stage "Plugin manifest audit completed."

$inventoryExtensions = @(".json", ".txt", ".enc", ".log", ".pdf", ".png", ".xlsx", ".xls", ".csv")
$inventory = @()
foreach ($file in $repoFiles) {
    $rel = Rel $file.FullName
    $isSchedule = $file.Name -match "\.schedule\.json$"
    $isManaged = $rel -match "^Plugins/(Medi_Track/medi_track|Projects_Track/(documents|sop))/"
    $isCandidate = $inventoryExtensions -contains $file.Extension.ToLowerInvariant()
    if ($isCandidate -or $isSchedule -or $isManaged) {
        $inventory += [pscustomobject]@{
            path = $rel
            bytes = [long]$file.Length
            sha256 = Sha $file.FullName
            classification = Classify-Path $rel
        }
    }
}
Stage "Repository data/resource inventory completed."

$managedRoots = @(
    "Plugins\Medi_Track\medi_track",
    "Plugins\Projects_Track\documents",
    "Plugins\Projects_Track\sop"
)
$managedPayloads = @()
foreach ($rootRel in $managedRoots) {
    $root = Join-Path $RepoRoot $rootRel
    if (-not (Test-Path -LiteralPath $root)) { continue }
    foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File) {
        $managedPayloads += [pscustomobject]@{
            path = Rel $file.FullName
            bytes = [long]$file.Length
            sha256 = Sha $file.FullName
            zero_byte = ($file.Length -eq 0)
        }
    }
}
Stage "Managed payload inventory completed."

$corePath = Join-Path $LegacyArchiveRoot "progtrack_daten.json"
$core = Read-Json $corePath
$animalMap = @{}
foreach ($storeName in @("animals", "archived_animals")) {
    $store = $core.PSObject.Properties[$storeName].Value
    foreach ($prop in $store.PSObject.Properties) {
        $animalMap[$prop.Name] = $prop.Value
    }
}
$animalKeys = @($animalMap.Keys)

$relationshipFields = @("eizellspenderin", "samenspender", "ziehmutter", "ziehvater")
$relationshipTotal = 0
$relationshipUnresolved = @()
$originViolations = @()
$catalogPath = Join-Path $RepoRoot "Plugins\Resources\Animal_Origins.txt"
$originCatalog = @(Get-Content -LiteralPath $catalogPath -Encoding utf8 | ForEach-Object { $_.Trim() } | Where-Object { $_ })
foreach ($ipid in $animalKeys) {
    $record = $animalMap[$ipid]
    foreach ($field in $relationshipFields) {
        $value = [string]$record.$field
        if ($value.Trim()) {
            $relationshipTotal++
            if (-not $animalMap.ContainsKey($value)) {
                $relationshipUnresolved += [pscustomobject]@{ ipid = $ipid; field = $field; value = $value }
            }
        }
    }
    $parents = @($relationshipFields | ForEach-Object { [string]$record.$_ } | Where-Object { $_.Trim() })
    $species = [string]$record.species
    if (-not $species) { $species = ([string]$ipid -split " \| ")[1] }
    $expectedOrigin = "DPZ"
    if ($parents.Count -eq 0) {
        if ($species -match "^Macaca") { $expectedOrigin = "Aul$([char]0x00EB)" }
        elseif ($species -match "^Callitrix|^Callithrix") { $expectedOrigin = "Iluvatar" }
        elseif ($species -match "^Papio") { $expectedOrigin = "Morgoth" }
    }
    $actualOrigin = [string]$record.origin
    if ($actualOrigin -ne $expectedOrigin -or $originCatalog -notcontains $actualOrigin) {
        $originViolations += [pscustomobject]@{
            ipid = $ipid
            actual = $actualOrigin
            expected = $expectedOrigin
            catalogued = ($originCatalog -contains $actualOrigin)
        }
    }
}

$sampleReferences = @()
foreach ($pair in @(
    @("Plugins\Sample_Track\organs.json", "organs"),
    @("Plugins\Sample_Track\other.json", "other")
)) {
    $rows = Read-Json (Join-Path $LegacyArchiveRoot $pair[0])
    for ($i = 0; $i -lt $rows.Count; $i++) {
        $sampleReferences += [pscustomobject]@{
            store = $pair[1]
            row = $i
            ipid = [string]$rows[$i].animal_name
            resolves = $animalMap.ContainsKey([string]$rows[$i].animal_name)
        }
    }
}

$projectHistory = Read-Json (Join-Path $LegacyArchiveRoot "Plugins\Projects_Track\projects_history.json")
$projectReferences = New-Object System.Collections.ArrayList
function Walk-ProjectReferences($Value, [string]$Path) {
    if ($null -eq $Value) { return }
    if ($Value -is [System.Array]) {
        for ($i = 0; $i -lt $Value.Count; $i++) {
            Walk-ProjectReferences $Value[$i] "$Path[$i]"
        }
        return
    }
    if ($Value -is [pscustomobject]) {
        $ipidProperty = $Value.PSObject.Properties["ipid"]
        if ($null -ne $ipidProperty) {
            $ipid = [string]$ipidProperty.Value
            if ($ipid.Contains(" | ")) {
                $null = $projectReferences.Add([pscustomobject]@{
                    path = $Path
                    ipid = $ipid
                    resolves = $animalMap.ContainsKey($ipid)
                })
            }
        }
        foreach ($property in $Value.PSObject.Properties) {
            Walk-ProjectReferences $property.Value "$Path.$($property.Name)"
        }
    }
}
Walk-ProjectReferences $projectHistory "projects_history"

$roleCounts = @{}
foreach ($record in $animalMap.Values) {
    $role = [string]$record.rolle
    if (-not $roleCounts.ContainsKey($role)) { $roleCounts[$role] = 0 }
    $roleCounts[$role]++
}

$permissionsPath = Join-Path $RepoRoot "Plugins\Master_Track\permissions.py"
$permissionSource = Get-Content -LiteralPath $permissionsPath -Raw -Encoding utf8
$permissionIds = @([regex]::Matches(
    $permissionSource,
    '(?m)^\s*PERM_[A-Z0-9_]+\s*=\s*["'']([a-z][a-z0-9_]*\.[a-z0-9_.]+)["'']'
) |
    ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
$labels = Read-Json (Join-Path $RepoRoot "Plugins\Master_Track\permissions_labels.json")
$labelCoverage = @()
foreach ($language in Get-ObjectPropertyNames $labels) {
    $keys = @(Get-ObjectPropertyNames $labels.PSObject.Properties[$language].Value)
    $labelCoverage += [pscustomobject]@{
        language = $language
        label_count = $keys.Count
        missing_defined_permissions = @($permissionIds | Where-Object { $keys -notcontains $_ })
        unknown_label_permissions = @($keys | Where-Object { $permissionIds -notcontains $_ })
    }
}
# jobs.json is a historical mutable role/permission store.  It is archived
# with the other legacy inputs and must not be treated as a repository/runtime
# authority by this audit.
$jobs = Read-Json (Join-Path $LegacyArchiveRoot "Plugins\Master_Track\jobs.json")
$jobUnknownPermissions = @()
foreach ($job in Get-ObjectPropertyNames $jobs) {
    foreach ($permission in @($jobs.PSObject.Properties[$job].Value)) {
        if ($permissionIds -notcontains [string]$permission) {
            $jobUnknownPermissions += [pscustomobject]@{ job = $job; permission = [string]$permission }
        }
    }
}

$sourceFiles = @($repoFiles | Where-Object { $_.Extension -eq ".py" })
$databasePatterns = "import\s+(?:sqlite3|psycopg|psycopg2)|from\s+(?:sqlite3|psycopg|psycopg2)|\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE\s+TABLE)\b"
$measurementPattern = "_resolve_import_animal_key\([^\r\n]*create_missing=True"
$writeCallsites = [Collections.Generic.List[object]]::new()
$databaseCallsites = [Collections.Generic.List[object]]::new()
$measurementCreateMissingCallsites = [Collections.Generic.List[object]]::new()
$disabledPluginCallsites = [Collections.Generic.List[object]]::new()
foreach ($file in $sourceFiles) {
    $lineNumber = 0
    foreach ($line in Get-Content -LiteralPath $file.FullName -Encoding utf8) {
        $lineNumber++
        $trimmed = $line.Trim()
        $hasWriteOpenMode = (
            ($trimmed.Contains("open(") -or $trimmed.Contains(".open(")) -and
            $trimmed -cmatch '["''](?:w|a|x)[bt+]*["'']'
        )
        $hasWritePrimitive = (
            $trimmed.Contains("write_text(") -or
            $trimmed.Contains("write_bytes(") -or
            $trimmed.Contains("json.dump(") -or
            $trimmed.Contains("os.fdopen(") -or
            $trimmed.Contains("os.replace(") -or
            $trimmed.Contains("shutil.copy(") -or
            $trimmed.Contains("shutil.copy2(") -or
            $trimmed.Contains("shutil.copyfile(") -or
            $trimmed.Contains("shutil.move(") -or
            $trimmed.Contains(".mkdir(") -or
            $trimmed.Contains("os.makedirs(")
        )
        if (-not $trimmed.StartsWith("#") -and ($hasWriteOpenMode -or $hasWritePrimitive)) {
            $writeCallsites.Add([pscustomobject]@{ path = Rel $file.FullName; line = $lineNumber; text = $line.Trim() })
        }
        if (-not $trimmed.StartsWith("#") -and $line -cmatch $databasePatterns) {
            $databaseCallsites.Add([pscustomobject]@{ path = Rel $file.FullName; line = $lineNumber; text = $line.Trim() })
        }
        if (-not $trimmed.StartsWith("#") -and $line -cmatch $measurementPattern) {
            $measurementCreateMissingCallsites.Add([pscustomobject]@{ path = Rel $file.FullName; line = $lineNumber; text = $line.Trim() })
        }
        if (-not $trimmed.StartsWith("#") -and $line -cmatch "_disabled_plugins|disabled_plugins") {
            $disabledPluginCallsites.Add([pscustomobject]@{ path = Rel $file.FullName; line = $lineNumber; text = $line.Trim() })
        }
    }
}
Stage "Python source scan completed."

$statusAfter = @(Git @("status", "--porcelain=v1", "--untracked-files=all"))
Stage "Final Git status captured."
$statusUnchanged = (($statusBefore -join "`n") -ceq ($statusAfter -join "`n"))

$checks = [ordered]@{
    manifest_count_is_13 = ($manifests.Count -eq 13)
    manifest_parse_failures_zero = ($manifestFailures.Count -eq 0)
    manifest_entry_modules_exist = (@($manifests | Where-Object { -not $_.entry_module_exists }).Count -eq 0)
    json_parse_failures_zero = ($jsonFailures.Count -eq 0)
    animal_count_is_227 = ($animalKeys.Count -eq 227)
    core_relationships_resolve = ($relationshipUnresolved.Count -eq 0)
    origin_rules_and_catalogue_pass = ($originViolations.Count -eq 0)
    sample_references_resolve = (@($sampleReferences | Where-Object { -not $_.resolves }).Count -eq 0)
    explicit_project_history_ipids_resolve = (@($projectReferences | Where-Object { -not $_.resolves }).Count -eq 0)
    permission_jobs_reference_defined_ids = ($jobUnknownPermissions.Count -eq 0)
    permission_labels_match_defined_ids = (@($labelCoverage | Where-Object {
        $_.missing_defined_permissions.Count -ne 0 -or
        $_.unknown_label_permissions.Count -ne 0
    }).Count -eq 0)
    worktree_status_unchanged = $statusUnchanged
}
$passed = @($checks.Values | Where-Object { -not $_ }).Count -eq 0

$result = [ordered]@{
    schema_version = $schemaVersion
    verifier_version = $scriptVersion
    generated_at_utc = [DateTime]::UtcNow.ToString("o")
    invocation = [ordered]@{
        script = $MyInvocation.MyCommand.Path
        repo_root = $RepoRoot
        output_path = $OutputPath
        powershell = $PSVersionTable.PSVersion.ToString()
        command = "& `"$($MyInvocation.MyCommand.Path)`" -RepoRoot `"$RepoRoot`" -OutputPath `"$OutputPath`""
    }
    repository = [ordered]@{
        branch = $branch
        commit = $commit
        tracked_files = $trackedCount
        status_before = $statusBefore
        status_after = $statusAfter
        status_unchanged = $statusUnchanged
        clean_baseline = ($statusBefore.Count -eq 0)
    }
    checks = $checks
    passed = $passed
    expected_current_findings = [ordered]@{
        measurement_create_missing_callsites = $measurementCreateMissingCallsites.Count
        direct_write_callsites = $writeCallsites.Count
        database_or_sql_callsites = $databaseCallsites.Count
        managed_zero_byte_payloads = @($managedPayloads | Where-Object { $_.zero_byte }).Count
    }
    manifests = [ordered]@{
        records = $manifests
        failures = $manifestFailures
    }
    persistence_inventory = $inventory
    managed_payloads = $managedPayloads
    references = [ordered]@{
        animals = $animalKeys.Count
        relationships_checked = $relationshipTotal
        unresolved_relationships = $relationshipUnresolved
        origin_catalogue = $originCatalog
        origin_violations = $originViolations
        sample_references = $sampleReferences
        project_history_references = @($projectReferences)
        role_counts = $roleCounts
    }
    permissions = [ordered]@{
        defined_ids = $permissionIds
        label_coverage = $labelCoverage
        job_unknown_permissions = $jobUnknownPermissions
    }
    source_scan = [ordered]@{
        write_callsites = $writeCallsites
        database_or_sql_callsites = $databaseCallsites
        measurement_create_missing_callsites = $measurementCreateMissingCallsites
        disabled_plugin_callsites = $disabledPluginCallsites
    }
    json_parse_failures = $jsonFailures
    exit_rule = "0 only when all checks are true; expected current findings remain evidence, not verifier failures."
}

$json = $result | ConvertTo-Json -Depth 20
Stage "Evidence object serialized."
[IO.File]::WriteAllText($OutputPath, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
$hash = Sha $OutputPath
$hashPath = $OutputPath + ".sha256"
[IO.File]::WriteAllText($hashPath, "$hash  $([IO.Path]::GetFileName($OutputPath))" + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

Write-Output "Result: $OutputPath"
Write-Output "SHA-256: $hash"
Write-Output "Passed: $passed"
Write-Output "Repository status unchanged: $statusUnchanged"

if (-not $passed) { exit 1 }
