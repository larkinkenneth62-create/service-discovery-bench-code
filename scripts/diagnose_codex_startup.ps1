param(
    [int]$DurationSeconds = 90
)

$ErrorActionPreference = 'SilentlyContinue'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outputPath = Join-Path $workspaceRoot "tmp\codex-startup-monitor-$stamp.csv"
$statusPath = Join-Path $workspaceRoot 'tmp\codex-startup-monitor-status.json'
$logicalCores = [Environment]::ProcessorCount

Add-Type -AssemblyName Microsoft.VisualBasic
$computerInfo = New-Object Microsoft.VisualBasic.Devices.ComputerInfo

$startedAt = Get-Date
[pscustomobject]@{
    state = 'running'
    monitor_pid = $PID
    started_at = $startedAt.ToString('o')
    duration_seconds = $DurationSeconds
    csv_path = $outputPath
} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8

$previousCpu = @{}
$previousAt = Get-Date
$sampleNumber = 0
$lastGpuTop = ''
$lastGpuTopPct = 0.0
$lastGpuTotalPct = 0.0
$lastPageReads = 0.0
$lastDiskQueue = 0.0

while (((Get-Date) - $startedAt).TotalSeconds -lt $DurationSeconds) {
    Start-Sleep -Milliseconds 1000
    $now = Get-Date
    $intervalSeconds = [math]::Max(($now - $previousAt).TotalSeconds, 0.1)
    $processes = @(Get-Process)
    $currentCpu = @{}
    $cpuRows = @()

    foreach ($process in $processes) {
        if ($null -eq $process.CPU) {
            continue
        }

        $currentCpu[$process.Id] = $process.CPU
        if ($previousCpu.ContainsKey($process.Id)) {
            $delta = $process.CPU - $previousCpu[$process.Id]
            if ($delta -gt 0) {
                $allCorePct = $delta / $intervalSeconds / $logicalCores * 100
                $cpuRows += [pscustomobject]@{
                    Name = $process.ProcessName
                    PID = $process.Id
                    Percent = $allCorePct
                }
            }
        }
    }

    $totalCpuPct = ($cpuRows | Measure-Object Percent -Sum).Sum
    if ($null -eq $totalCpuPct) {
        $totalCpuPct = 0
    }

    $topCpu = $cpuRows |
        Sort-Object Percent -Descending |
        Select-Object -First 8 |
        ForEach-Object { '{0}:{1}:{2:N1}%' -f $_.Name, $_.PID, $_.Percent }

    $ramByName = @{}
    foreach ($group in ($processes | Group-Object ProcessName)) {
        $ramByName[$group.Name] = (($group.Group | Measure-Object WorkingSet64 -Sum).Sum / 1MB)
    }

    $chatProcesses = @($processes | Where-Object { $_.ProcessName -eq 'ChatGPT' })
    $chatStarts = @()
    foreach ($chatProcess in $chatProcesses) {
        try {
            $chatStarts += $chatProcess.StartTime
        } catch {
        }
    }
    $newestChatStart = $chatStarts | Sort-Object -Descending | Select-Object -First 1

    if (($sampleNumber % 2) -eq 0) {
        $counterSet = Get-Counter @(
            '\Memory\Page Reads/sec',
            '\PhysicalDisk(_Total)\Avg. Disk Queue Length',
            '\GPU Engine(*)\Utilization Percentage'
        )

        $pageSample = $counterSet.CounterSamples |
            Where-Object { $_.Path -like '*\memory\page reads/sec' } |
            Select-Object -First 1
        if ($pageSample) {
            $lastPageReads = $pageSample.CookedValue
        }

        $diskSample = $counterSet.CounterSamples |
            Where-Object { $_.Path -like '*\physicaldisk(_total)\avg. disk queue length' } |
            Select-Object -First 1
        if ($diskSample) {
            $lastDiskQueue = $diskSample.CookedValue
        }

        $gpuByPid = @{}
        foreach ($gpuSample in ($counterSet.CounterSamples | Where-Object { $_.Path -like '*\gpu engine(*' })) {
            if ($gpuSample.InstanceName -match '^pid_(\d+)_') {
                $gpuPid = [int]$Matches[1]
                if (-not $gpuByPid.ContainsKey($gpuPid)) {
                    $gpuByPid[$gpuPid] = 0.0
                }
                $gpuByPid[$gpuPid] += $gpuSample.CookedValue
            }
        }

        $gpuRows = foreach ($gpuPid in $gpuByPid.Keys) {
            $owner = $processes | Where-Object { $_.Id -eq $gpuPid } | Select-Object -First 1
            [pscustomobject]@{
                PID = $gpuPid
                Name = if ($owner) { $owner.ProcessName } else { 'exited' }
                Percent = $gpuByPid[$gpuPid]
            }
        }
        $gpuTopRow = $gpuRows | Sort-Object Percent -Descending | Select-Object -First 1
        if ($gpuTopRow) {
            $lastGpuTop = '{0}:{1}' -f $gpuTopRow.Name, $gpuTopRow.PID
            $lastGpuTopPct = $gpuTopRow.Percent
            $lastGpuTotalPct = ($gpuRows | Measure-Object Percent -Sum).Sum
        }
    }

    $row = [pscustomobject]@{
        Timestamp = $now.ToString('o')
        ElapsedSec = [math]::Round(($now - $startedAt).TotalSeconds, 2)
        TotalCpuPct = [math]::Round($totalCpuPct, 2)
        FreeRamMB = [math]::Round($computerInfo.AvailablePhysicalMemory / 1MB, 0)
        PageReadsPerSec = [math]::Round($lastPageReads, 2)
        DiskQueue = [math]::Round($lastDiskQueue, 3)
        ProcessCount = $processes.Count
        ChatGPTCount = $chatProcesses.Count
        NewestChatGPTStart = if ($newestChatStart) { $newestChatStart.ToString('o') } else { '' }
        ChatGPTRamMB = [math]::Round($ramByName['ChatGPT'], 1)
        CodexRamMB = [math]::Round($ramByName['codex'], 1)
        BrowserRamMB = [math]::Round($ramByName['browser'], 1)
        EdgeRamMB = [math]::Round($ramByName['msedge'], 1)
        WebViewRamMB = [math]::Round($ramByName['msedgewebview2'], 1)
        GameViewerRamMB = [math]::Round($ramByName['GameViewerServer'], 1)
        KasperskyRamMB = [math]::Round($ramByName['avp'], 1)
        GpuTop = $lastGpuTop
        GpuTopPct = [math]::Round($lastGpuTopPct, 2)
        GpuTotalPct = [math]::Round($lastGpuTotalPct, 2)
        TopCpu = ($topCpu -join '; ')
    }

    if ($sampleNumber -eq 0) {
        $row | Export-Csv -LiteralPath $outputPath -NoTypeInformation -Encoding UTF8
    } else {
        $row | Export-Csv -LiteralPath $outputPath -NoTypeInformation -Encoding UTF8 -Append
    }

    $previousCpu = $currentCpu
    $previousAt = $now
    $sampleNumber += 1
}

[pscustomobject]@{
    state = 'complete'
    monitor_pid = $PID
    started_at = $startedAt.ToString('o')
    completed_at = (Get-Date).ToString('o')
    duration_seconds = $DurationSeconds
    samples = $sampleNumber
    csv_path = $outputPath
} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
