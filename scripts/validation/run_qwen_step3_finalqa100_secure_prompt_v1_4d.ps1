param(
    [string]$Model = "qwen-plus",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    [string]$StructuredMode = "plain_json",
    [int]$MaxTokens = 2000,
    [int]$MaxWorkers = 4,
    [switch]$VisibleKeyInput
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectRoot

Write-Host "This script runs Qwen Step3 finalQA100 only."
Write-Host "It will not run full2168/full cleaning/final clean dataset/split/baseline/training."
Write-Host "The API key will be kept in this PowerShell process environment only and will not be written to files."
Write-Host "Project root: $ProjectRoot"
if ($VisibleKeyInput) {
    Write-Host "WARNING: -VisibleKeyInput shows the key on screen while typing/pasting. Use only when no one can see your screen."
}

$requestPath = "outputs\qwen_semcap_judge_v1_4d_step3\finalqa100\requests\qwen_step3_requests_finalqa100.jsonl"
$reviewedPath = "outputs\final_qa_v1_5e\final_qa_review_items_v1_5e_gpt_manual_reviewed.csv"
$outputCsv = "outputs\qwen_semcap_judge_v1_4d_step3\finalqa100\predictions\qwen_step3_predictions_finalqa100.csv"
$rawJsonl = "outputs\qwen_semcap_judge_v1_4d_step3\finalqa100\predictions\qwen_step3_raw_finalqa100.jsonl"

if (!(Test-Path $requestPath)) {
    throw "Missing finalQA100 request JSONL: $requestPath"
}
if (!(Test-Path $reviewedPath)) {
    throw "Missing final QA reviewed CSV: $reviewedPath"
}
if (Test-Path $outputCsv) {
    throw "Refusing to overwrite existing finalQA100 predictions: $outputCsv"
}
if (Test-Path $rawJsonl) {
    throw "Refusing to overwrite existing finalQA100 raw output: $rawJsonl"
}

$requestRows = @(Get-Content $requestPath | Where-Object { $_.Trim().Length -gt 0 })
$reviewRows = @(Import-Csv $reviewedPath)
if ($requestRows.Count -ne 100) {
    throw "Expected finalQA100 request count 100, got $($requestRows.Count)"
}
if ($reviewRows.Count -ne 100) {
    throw "Expected reviewed human row count 100, got $($reviewRows.Count)"
}

$sample20Report = "docs\phase1\qwen_step3_sample20_report_v1_4d.md"
$calibrationEvalReport = "docs\phase1\qwen_step3_calibration_eval_report_v1_4d.md"
$calibrationGoNoGoReport = "docs\phase1\qwen_step3_calibration_go_no_go_v1_4d.md"
foreach ($path in @($sample20Report, $calibrationEvalReport, $calibrationGoNoGoReport)) {
    if (!(Test-Path $path)) {
        throw "Missing required prior report: $path"
    }
}

$bstr = [IntPtr]::Zero
try {
    if ($VisibleKeyInput) {
        $plainKey = Read-Host "Paste Qwen/DashScope API key"
    }
    else {
        $secureKey = Read-Host "Paste Qwen/DashScope API key" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw "Empty API key."
    }

    $env:QWEN_API_KEY = $plainKey
    $env:QWEN_API_MODEL = $Model
    $env:QWEN_API_BASE_URL = $BaseUrl
    $env:QWEN_STRUCTURED_MODE = $StructuredMode
    $env:QWEN_THINKING = "disabled"

    python scripts\validation\run_qwen_semcap_judge_v1_4d_step3.py `
        --input-jsonl $requestPath `
        --output-csv $outputCsv `
        --raw-output-jsonl $rawJsonl `
        --model $Model `
        --base-url $BaseUrl `
        --temperature 0 `
        --max-workers $MaxWorkers `
        --max-tokens $MaxTokens `
        --structured-mode $StructuredMode `
        --thinking disabled
}
finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    Remove-Item Env:\QWEN_API_KEY -ErrorAction SilentlyContinue
}
