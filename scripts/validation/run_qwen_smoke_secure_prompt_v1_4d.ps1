param(
    [string]$Model = "qwen-plus",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    [switch]$TryCommonModels,
    [switch]$TryCommonBaseUrls,
    [switch]$TryDashScopeNative,
    [string]$DashScopeBaseUrl = "",
    [switch]$VisibleKeyInput
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectRoot

Write-Host "This script runs a minimal Qwen smoke test only."
Write-Host "It does not use benchmark data and will not run sample20/calibration/full cleaning/split/baseline/training."
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

    $argsList = @(
        "scripts\validation\diagnose_qwen_api_smoke_v1_4d.py",
        "--model", $Model,
        "--base-url", $BaseUrl
    )
    if ($TryCommonModels) {
        $argsList += "--try-common-models"
    }
    if ($TryCommonBaseUrls) {
        $argsList += "--try-common-base-urls"
    }
    if ($TryDashScopeNative) {
        $argsList += "--try-dashscope-native"
    }
    if (-not [string]::IsNullOrWhiteSpace($DashScopeBaseUrl)) {
        $argsList += @("--dashscope-base-url", $DashScopeBaseUrl)
    }
    python @argsList
}
finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    Remove-Item Env:\QWEN_API_KEY -ErrorAction SilentlyContinue
}
