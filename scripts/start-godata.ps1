[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$installDir = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $installDir ".venv\Scripts\python.exe"
$cloudflaredExe = Join-Path $installDir "bin\cloudflared.exe"
$envFile = Join-Path $installDir ".env"
$logDir = Join-Path $installDir "logs"
$mutex = New-Object System.Threading.Mutex($false, "Local\GoData-$([System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value)")
$ownsMutex = $false
$appProcess = $null
$tunnelProcess = $null

try {
    $ownsMutex = $mutex.WaitOne(0, $false)
    if (-not $ownsMutex) {
        Write-Host "O GoData já está em execução para este usuário."
        exit 0
    }

    if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Python virtual do GoData não encontrado: $pythonExe" }
    if (-not (Test-Path -LiteralPath $cloudflaredExe)) { throw "cloudflared não encontrado: $cloudflaredExe" }
    if (-not (Test-Path -LiteralPath $envFile)) { throw "Configuração não encontrada: $envFile" }

    $apiKeyLine = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^GODATA_API_KEY=' } | Select-Object -First 1
    if (-not $apiKeyLine) { throw "GODATA_API_KEY não está configurada em $envFile" }
    $apiKey = $apiKeyLine.Substring("GODATA_API_KEY=".Length).Trim()

    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $tunnelOut = Join-Path $logDir "cloudflared.out.log"
    $tunnelErr = Join-Path $logDir "cloudflared.err.log"
    Remove-Item -LiteralPath $tunnelOut, $tunnelErr -Force -ErrorAction SilentlyContinue

    $appDir = Join-Path $installDir "src"
    $appArgs = '-m uvicorn godata.main:app --app-dir "{0}" --host 127.0.0.1 --port 4400' -f $appDir
    $appProcess = Start-Process -FilePath $pythonExe -ArgumentList $appArgs -WorkingDirectory $installDir -NoNewWindow -PassThru

    $healthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($appProcess.HasExited) { throw "O GoData encerrou durante a inicialização (código $($appProcess.ExitCode))." }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:4400/health" -TimeoutSec 2
            if ($health.status -eq "ok") { $healthy = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthy) { throw "O GoData não respondeu em http://127.0.0.1:4400/health." }

    $tunnelProcess = Start-Process -FilePath $cloudflaredExe `
        -ArgumentList @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:4400") `
        -WorkingDirectory $installDir -RedirectStandardOutput $tunnelOut -RedirectStandardError $tunnelErr -PassThru

    $tunnelUrl = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($tunnelProcess.HasExited) { break }
        $output = @()
        if (Test-Path -LiteralPath $tunnelOut) { $output += Get-Content -LiteralPath $tunnelOut -Raw }
        if (Test-Path -LiteralPath $tunnelErr) { $output += Get-Content -LiteralPath $tunnelErr -Raw }
        $match = [regex]::Match(($output -join "`n"), 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($match.Success) { $tunnelUrl = $match.Value; break }
        Start-Sleep -Seconds 1
    }

    Clear-Host
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host " GoData iniciado com Cloudflare Tunnel" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    if ($tunnelUrl) {
        Write-Host "URL:       $tunnelUrl" -ForegroundColor Green
        Write-Host "Swagger:   $tunnelUrl/docs" -ForegroundColor Green
    } else {
        Write-Host "Não foi possível obter a URL do túnel." -ForegroundColor Red
        Write-Host "Logs: $tunnelErr" -ForegroundColor Yellow
    }
    Write-Host "API Key:   $apiKey" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Mantenha esta janela aberta. Pressione Ctrl+C para encerrar."

    if (-not $tunnelUrl) { throw "O cloudflared não publicou uma URL TryCloudflare." }
    Wait-Process -Id $tunnelProcess.Id
} catch {
    Write-Host ""
    Write-Host "Falha ao iniciar o GoData: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Pressione Enter para fechar."
    [void](Read-Host)
    exit 1
} finally {
    if ($tunnelProcess -and -not $tunnelProcess.HasExited) { Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($appProcess -and -not $appProcess.HasExited) { Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
