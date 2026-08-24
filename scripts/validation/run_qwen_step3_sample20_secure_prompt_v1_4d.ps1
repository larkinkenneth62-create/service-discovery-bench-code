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

Write-Host "This script runs Qwen Step3 sample20 only."
Write-Host "It will not run calibration180/full2168/full cleaning/split/baseline/training."
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

    python scripts\validation\run_qwen_semcap_judge_v1_4d_step3.py `
        --input-jsonl outputs\qwen_semcap_judge_v1_4d_step3\requests\qwen_step3_request_sample_20.jsonl `
        --output-csv outputs\qwen_semcap_judge_v1_4d_step3\predictions\qwen_step3_predictions_sample_20.csv `
        --raw-output-jsonl outputs\qwen_semcap_judge_v1_4d_step3\predictions\qwen_step3_raw_sample_20.jsonl `
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
