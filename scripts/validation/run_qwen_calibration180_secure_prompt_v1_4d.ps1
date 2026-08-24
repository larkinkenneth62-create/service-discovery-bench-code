param(
    [string]$Model = "qwen-plus",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    [string]$StructuredMode = "plain_json",
    [int]$MaxTokens = 1600,
    [int]$MaxWorkers = 6,
    [switch]$VisibleKeyInput
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectRoot

Write-Host "This script runs Qwen calibration180 only."
Write-Host "It will not run full2168/full cleaning/split/baseline/training."
Write-Host "The API key will be kept in this PowerShell process environment only and will not be written to files."
Write-Host "Project root: $ProjectRoot"
if ($VisibleKeyInput) {
    Write-Host "WARNING: -VisibleKeyInput shows the key on screen while typing/pasting. Use only when no one can see your screen."
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

    Write-Host "Running calibration180 preflight on 1 item..."
    python scripts\validation\run_qwen_semcap_judge_v1_4d.py `
        --input-jsonl outputs\qwen_semcap_judge_v1_4d\requests\qwen_semcap_requests_calibration_180.jsonl `
        --output-csv outputs\qwen_semcap_judge_v1_4d\predictions\qwen_semcap_predictions_calibration_preflight_1.csv `
        --raw-output-jsonl outputs\qwen_semcap_judge_v1_4d\predictions\qwen_semcap_raw_calibration_preflight_1.jsonl `
        --model $Model `
        --base-url $BaseUrl `
        --temperature 0 `
        --max-workers 1 `
        --max-tokens $MaxTokens `
        --limit 1 `
        --structured-mode $StructuredMode `
        --thinking disabled

    $preflightRows = @(Import-Csv "outputs\qwen_semcap_judge_v1_4d\predictions\qwen_semcap_predictions_calibration_preflight_1.csv")
    if ($preflightRows.Count -lt 1 -or $preflightRows[0].QWEN_parse_status -ne "ok") {
        $status = if ($preflightRows.Count -ge 1) { $preflightRows[0].QWEN_parse_status } else { "missing_row" }
        throw "Calibration preflight failed with parse_status=$status. Full calibration180 was not run. Check outputs\qwen_semcap_judge_v1_4d\predictions\qwen_semcap_raw_calibration_preflight_1.jsonl"
    }
    Write-Host "Preflight passed. Running calibration180 with resume; only ok rows are skipped, failed rows are retried."

    python scripts\validation\run_qwen_semcap_judge_v1_4d.py `
        --input-jsonl outputs\qwen_semcap_judge_v1_4d\requests\qwen_semcap_requests_calibration_180.jsonl `
        --output-csv outputs\qwen_semcap_judge_v1_4d\predictions\qwen_semcap_predictions_calibration_180.csv `
        --raw-output-jsonl outputs\qwen_semcap_judge_v1_4d\predictions\qwen_semcap_raw_calibration_180.jsonl `
        --model $Model `
        --base-url $BaseUrl `
        --temperature 0 `
        --max-workers $MaxWorkers `
        --max-tokens $MaxTokens `
        --structured-mode $StructuredMode `
        --thinking disabled `
        --resume
}
finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    Remove-Item Env:\QWEN_API_KEY -ErrorAction SilentlyContinue
}
