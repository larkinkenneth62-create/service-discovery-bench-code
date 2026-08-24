param(
    [string]$Root = (Resolve-Path ".").Path
)

$ErrorActionPreference = "Stop"

$required = @(
    [PSCustomObject]@{ Source = "ToolBench"; Path = "external_sources/ToolBench/data/instruction/G1_query.json"; Required = "yes" },
    [PSCustomObject]@{ Source = "ToolBench"; Path = "external_sources/ToolBench/data/instruction/G2_query.json"; Required = "yes" },
    [PSCustomObject]@{ Source = "ToolBench"; Path = "external_sources/ToolBench/data/instruction/G3_query.json"; Required = "yes" },
    [PSCustomObject]@{ Source = "ToolBench"; Path = "external_sources/ToolBench/data/test_instruction/G1_instruction.json"; Required = "yes" },
    [PSCustomObject]@{ Source = "ToolBench"; Path = "external_sources/ToolBench/data/test_instruction/G2_instruction.json"; Required = "yes" },
    [PSCustomObject]@{ Source = "ToolBench"; Path = "external_sources/ToolBench/data/test_instruction/G3_instruction.json"; Required = "yes" },
    [PSCustomObject]@{ Source = "ToolBench"; Path = "external_sources/ToolBench/data/answer"; Required = "yes" },
    [PSCustomObject]@{ Source = "ToolBench"; Path = "external_sources/ToolBench/reproduction_data"; Required = "yes" },

    [PSCustomObject]@{ Source = "StableToolBench"; Path = "external_sources/StableToolBench/solvable_queries/test_instruction/G1_instruction.json"; Required = "yes" },
    [PSCustomObject]@{ Source = "StableToolBench"; Path = "external_sources/StableToolBench/solvable_queries/test_instruction/G2_instruction.json"; Required = "yes" },
    [PSCustomObject]@{ Source = "StableToolBench"; Path = "external_sources/StableToolBench/solvable_queries/test_instruction/G3_instruction.json"; Required = "yes" },

    [PSCustomObject]@{ Source = "MetaTool"; Path = "external_sources/MetaTool/dataset/data/all_clean_data.csv"; Required = "yes" },
    [PSCustomObject]@{ Source = "MetaTool"; Path = "external_sources/MetaTool/dataset/plugin_des.json"; Required = "yes" },

    [PSCustomObject]@{ Source = "ShortcutsBench"; Path = "external_sources/ShortcutsBench/generated_success_queries.json"; Required = "archive" },
    [PSCustomObject]@{ Source = "ShortcutsBench"; Path = "external_sources/ShortcutsBench/generated_success_queries.json.extracted"; Required = "yes" },
    [PSCustomObject]@{ Source = "ShortcutsBench"; Path = "external_sources/ShortcutsBench/1_final_detailed_records_filter_apis_leq_30.json"; Required = "archive" },
    [PSCustomObject]@{ Source = "ShortcutsBench"; Path = "external_sources/ShortcutsBench/1_final_detailed_records_filter_apis_leq_30.json.extracted"; Required = "yes" },
    [PSCustomObject]@{ Source = "ShortcutsBench"; Path = "external_sources/ShortcutsBench/4_api_json_filter.json"; Required = "archive" },
    [PSCustomObject]@{ Source = "ShortcutsBench"; Path = "external_sources/ShortcutsBench/4_api_json_filter.json.extracted"; Required = "yes" }
)

$rows = foreach ($item in $required) {
    $fullPath = Join-Path $Root $item.Path
    $exists = Test-Path -LiteralPath $fullPath
    $kind = "missing"
    $length = $null
    if ($exists) {
        $entry = Get-Item -LiteralPath $fullPath
        $kind = if ($entry.PSIsContainer) { "dir" } else { "file" }
        $length = if ($entry.PSIsContainer) { $null } else { $entry.Length }
    }
    [PSCustomObject]@{
        Source = $item.Source
        Required = $item.Required
        Exists = $exists
        Kind = $kind
        Length = $length
        Path = $item.Path
    }
}

$rows | Format-Table -AutoSize

$missingRequired = $rows | Where-Object { $_.Required -eq "yes" -and -not $_.Exists }
if ($missingRequired.Count -gt 0) {
    Write-Host ""
    Write-Host "Missing required entries: $($missingRequired.Count)" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "All required source entries are present." -ForegroundColor Green
