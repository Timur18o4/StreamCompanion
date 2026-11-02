$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot 'runtime\ollama.exe'
$models = Join-Path $PSScriptRoot 'models\ollama'
$modelLocation = Join-Path $PSScriptRoot 'models-location.json'
if (Test-Path -LiteralPath $modelLocation) {
    $location = Get-Content -LiteralPath $modelLocation -Raw | ConvertFrom-Json
    if ($location.modelsPath -and (Test-Path -LiteralPath $location.modelsPath -PathType Container)) {
        $models = [IO.Path]::GetFullPath([string]$location.modelsPath)
    } else {
        Write-Warning 'Der zusaetzliche Modellordner fehlt. Verwende die Modelle im Installationsordner.'
    }
}
$logs = Join-Path $PSScriptRoot 'logs'
$pidFile = Join-Path $PSScriptRoot 'server.pid'
$address = 'http://127.0.0.1:11434'

if (-not (Test-Path -LiteralPath $runtime)) {
    throw 'Die lokale KI-Laufzeit fehlt: local-ai\runtime\ollama.exe'
}
New-Item -ItemType Directory -Path $models, $logs -Force | Out-Null

function Test-LocalServer {
    try {
        $version = Invoke-RestMethod -Uri ($address + '/api/version') -TimeoutSec 2
        return [bool]$version.version
    } catch { return $false }
}

$process = $null
if (Test-Path -LiteralPath $pidFile) {
    $knownPid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$knownPid) -and $knownPid -gt 0) {
        $candidate = Get-Process -Id $knownPid -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.Path -eq $runtime) { $process = $candidate }
    }
}

if (Test-LocalServer) {
    if (-not $process) {
        throw 'Port 11434 ist bereits von einem KI-Server belegt, der nicht zu dieser Installation gehoert.'
    }
    Write-Output 'Lokaler CPU-KI-Server bereit. Das Modell wird bei der ersten Anfrage geladen.'
    exit 0
}

if (-not $process) {
    $env:OLLAMA_HOST = '127.0.0.1:11434'
    $env:OLLAMA_MODELS = $models
    $env:OLLAMA_NO_CLOUD = '1'
    $env:CUDA_VISIBLE_DEVICES = '-1'
    $env:GGML_VK_VISIBLE_DEVICES = '-1'
    $env:ROCR_VISIBLE_DEVICES = '-1'
    $env:OLLAMA_VULKAN = '0'
    $env:OLLAMA_NUM_PARALLEL = '1'
    $env:OLLAMA_MAX_LOADED_MODELS = '1'
    $env:OLLAMA_CONTEXT_LENGTH = '8192'
    $env:OLLAMA_KEEP_ALIVE = '2h'
    $process = Start-Process -FilePath $runtime -ArgumentList 'serve' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs 'server.stdout.log') -RedirectStandardError (Join-Path $logs 'server.stderr.log')
    [IO.File]::WriteAllText($pidFile, [string]$process.Id)
}

# Readiness means the server answers. Loading a model is deliberately not a
# prerequisite for opening the application, and uses its configured threads.
$timer = [Diagnostics.Stopwatch]::StartNew()
while ($timer.Elapsed.TotalSeconds -lt 35) {
    if (Test-LocalServer) {
        Write-Output 'Lokaler CPU-KI-Server bereit. Das Modell wird bei der ersten Anfrage geladen.'
        exit 0
    }
    $process.Refresh()
    if ($process.HasExited) { throw 'Der lokale KI-Server wurde beendet. Details stehen unter local-ai\logs.' }
    Start-Sleep -Milliseconds 500
}
throw 'Der lokale KI-Server antwortet nicht rechtzeitig. Details stehen unter local-ai\logs.'
