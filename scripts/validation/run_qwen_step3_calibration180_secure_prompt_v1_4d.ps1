param(
    [string]$Model = "qwen-plus",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    [string]$StructuredMode = "plain_json",
    [int]$MaxTokens = 2000,
    [int]$MaxWorkers = 6,
    [switch]$VisibleKeyInput
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectRoot

Write-Host "This script runs Qwen Step3 calibration180 only."
Write-Host "It will not run full2168/full cleaning/split/baseline/training."
Write-Host "Run this only after Step3 sample20 has parse_ok_rate >= 95%, schema_failed_count = 0, and invalid_enum_count = 0."
Write-Host "The API key will be kept in this PowerShell process environment only and will not be written to files."
Write-Host "Project root: $ProjectRoot"
if ($VisibleKeyInput) {
    Write-Host "WARNING: -VisibleKeyInput shows the key on screen while typing/pasting. Use only when no one can see your screen."
}

$sample20Path = "outputs\qwen_semcap_judge_v1_4d_step3\predictions\qwen_step3_predictions_sample_20.csv"
if (!(Test-Path $sample20Path)) {
    throw "Missing Step3 sample20 predictions. Run run_qwen_step3_sample20_secure_prompt_v1_4d.ps1 first."
}
$sampleRows = @(Import-Csv $sample20Path)
$okRows = @($sampleRows | Where-Object { $_.QWEN_parse_status -eq "ok" })
$schemaFailedRows = @($sampleRows | Where-Object { $_.QWEN_parse_status -eq "schema_failed" })
if ($sampleRows.Count -ne 20 -or $okRows.Count -lt 19 -or $schemaFailedRows.Count -ne 0) {
    throw "Step3 sample20 gate failed. rows=$($sampleRows.Count), ok=$($okRows.Count), schema_failed=$($schemaFailedRows.Count). Calibration180 was not run."
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
        --input-jsonl outputs\qwen_semcap_judge_v1_4d_step3\requests\qwen_step3_requests_calibration_180.jsonl `
        --output-csv outputs\qwen_semcap_judge_v1_4d_step3\predictions\qwen_step3_predictions_calibration_180.csv `
        --raw-output-jsonl outputs\qwen_semcap_judge_v1_4d_step3\predictions\qwen_step3_raw_calibration_180.jsonl `
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
