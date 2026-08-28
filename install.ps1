[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "GoData"),
    [switch]$RotateApiKey
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$repoArchive = "https://github.com/wanbnn/godata/archive/refs/heads/main.zip"
$pythonInstallerUrls = @{
    "AMD64" = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
    "ARM64" = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-arm64.exe"
    "x86"   = "https://www.python.org/ftp/python/3.12.10/python-3.12.10.exe"
}
$odbcUrls = @{
    "AMD64" = "https://download.microsoft.com/download/7bf9fad4-0f21-486d-a750-fc990ded5624/amd64/1033/msodbcsql.msi"
    "ARM64" = "https://download.microsoft.com/download/76504d2d-06b3-4262-8bc9-855ffd08d7be/arm64/1033/msodbcsql.msi"
    "x86"   = "https://download.microsoft.com/download/c0d0dcf1-bd9b-46ec-a659-5046ee11d1d1/x86/1033/msodbcsql.msi"
}

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Download-File([string]$Uri, [string]$Destination) {
    $errors = @()
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing
        return
    } catch { $errors += $_.Exception.Message }

    try {
        (New-Object Net.WebClient).DownloadFile($Uri, $Destination)
        return
    } catch { $errors += $_.Exception.Message }

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source -fL $Uri -o $Destination
        if ($LASTEXITCODE -eq 0) { return }
        $errors += "curl.exe retornou $LASTEXITCODE"
    }
    throw "Falha ao baixar $Uri. $($errors -join ' | ')"
}

function Get-CompatiblePython {
    $candidates = @()
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) { $candidates += $python.Source }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($selector in @("-3.13", "-3.12", "-3.11")) {
            try {
                $resolved = & $py.Source $selector -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $resolved) { $candidates += $resolved.Trim() }
            } catch {}
        }
    }
    $known = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe")
    )
    $candidates += $known

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            & $candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch {}
    }
    return $null
}

function Test-OdbcDriver {
    $paths = @(
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 18 for SQL Server",
        "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\ODBC\ODBCINST.INI\ODBC Driver 18 for SQL Server"
    )
    return [bool]($paths | Where-Object { Test-Path $_ } | Select-Object -First 1)
}

function New-ApiKey {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Register-GoDataStartup([string]$PythonExe, [string]$Launcher) {
    $command = '"{0}" "{1}"' -f $PythonExe, $Launcher

    Write-Step "Configurando inicialização automática"
    try {
        & schtasks.exe /Create /TN "GoData" /SC ONLOGON /TR $command /RL LIMITED /IT /F 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            & schtasks.exe /Query /TN "GoData" | Out-Null
            if ($LASTEXITCODE -eq 0) { return "Agendador de Tarefas" }
        }
    } catch { Write-Host "Agendador indisponível; tentando Registro..." -ForegroundColor Yellow }

    try {
        $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
        New-Item -Path $runKey -Force | Out-Null
        New-ItemProperty -Path $runKey -Name "GoData" -Value $command -PropertyType String -Force | Out-Null
        if ((Get-ItemPropertyValue -Path $runKey -Name "GoData") -eq $command) { return "Registro Run do usuário" }
    } catch { Write-Host "Registro Run indisponível; tentando pasta Startup..." -ForegroundColor Yellow }

    try {
        $startup = [Environment]::GetFolderPath("Startup")
        if (-not $startup) { throw "Pasta Startup não localizada" }
        $startupLink = Join-Path $startup "GoData.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($startupLink)
        $shortcut.TargetPath = $PythonExe
        $shortcut.Arguments = '"{0}"' -f $Launcher
        $shortcut.WorkingDirectory = Split-Path -Parent (Split-Path -Parent $Launcher)
        $shortcut.Description = "GoData com Cloudflare Tunnel"
        $shortcut.Save()
        if (Test-Path -LiteralPath $startupLink) { return "Atalho na pasta Startup do usuário" }
    } catch { throw "Nenhum método de inicialização foi permitido por este Windows: $($_.Exception.Message)" }

    throw "Não foi possível registrar a inicialização automática."
}

$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("godata-install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

try {
    Write-Step "Baixando o GoData"
    $archive = Join-Path $tempDir "godata.zip"
    Download-File $repoArchive $archive
    Expand-Archive -LiteralPath $archive -DestinationPath $tempDir -Force
    $sourceDir = Get-ChildItem -LiteralPath $tempDir -Directory | Where-Object { $_.Name -like "godata-*" } | Select-Object -First 1
    if (-not $sourceDir) { throw "Conteúdo do GoData não encontrado no pacote baixado." }
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceDir.FullName -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $InstallDir -Recurse -Force
    }

    Write-Step "Verificando Python 3.11+"
    $pythonExe = Get-CompatiblePython
    if (-not $pythonExe) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if ($winget) {
            & $winget.Source install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
            $pythonExe = Get-CompatiblePython
        }
    }
    if (-not $pythonExe) {
        Write-Host "Python não encontrado via PATH/winget; usando instalador oficial por usuário." -ForegroundColor Yellow
        $pythonInstaller = Join-Path $tempDir "python-installer.exe"
        $pythonArchitecture = $env:PROCESSOR_ARCHITECTURE
        if (-not $pythonInstallerUrls.ContainsKey($pythonArchitecture)) { $pythonArchitecture = "x86" }
        Download-File $pythonInstallerUrls[$pythonArchitecture] $pythonInstaller
        $process = Start-Process -FilePath $pythonInstaller -ArgumentList @(
            "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_pip=1"
        ) -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Instalador do Python retornou $($process.ExitCode)." }
        $pythonExe = Get-CompatiblePython
    }
    if (-not $pythonExe) { throw "Não foi possível instalar/localizar Python 3.11 ou superior." }
    Write-Host "Python: $pythonExe" -ForegroundColor Green

    if (-not (Test-OdbcDriver)) {
        Write-Step "Instalando Microsoft ODBC Driver 18 for SQL Server"
        $architecture = $env:PROCESSOR_ARCHITECTURE
        if (-not $odbcUrls.ContainsKey($architecture)) { $architecture = "x86" }
        $odbcInstaller = Join-Path $tempDir "msodbcsql.msi"
        Download-File $odbcUrls[$architecture] $odbcInstaller
        $arguments = "/i `"$odbcInstaller`" /qn /norestart IACCEPTMSODBCSQLLICENSETERMS=YES"
        $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator
        )
        if ($isAdmin) {
            $process = Start-Process msiexec.exe -ArgumentList $arguments -Wait -PassThru
        } else {
            Write-Host "O Windows pode solicitar confirmação UAC para instalar o driver oficial." -ForegroundColor Yellow
            $process = Start-Process msiexec.exe -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        }
        if ($process.ExitCode -notin @(0, 3010)) { throw "Instalador ODBC retornou $($process.ExitCode)." }
        if (-not (Test-OdbcDriver)) { throw "ODBC Driver 18 não foi detectado após a instalação." }
    } else {
        Write-Host "ODBC Driver 18 já instalado." -ForegroundColor Green
    }

    Write-Step "Instalando cloudflared"
    $binDir = Join-Path $InstallDir "bin"
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $cloudflaredArch = switch ($env:PROCESSOR_ARCHITECTURE) {
        "ARM64" { "arm64" }
        "x86" { "386" }
        default { "amd64" }
    }
    $cloudflaredExe = Join-Path $binDir "cloudflared.exe"
    Download-File "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-$cloudflaredArch.exe" $cloudflaredExe
    & $cloudflaredExe --version
    if ($LASTEXITCODE -ne 0) { throw "cloudflared baixado não pôde ser executado." }

    Write-Step "Criando ambiente Python isolado"
    $venvDir = Join-Path $InstallDir ".venv"
    if (-not (Test-Path -LiteralPath (Join-Path $venvDir "Scripts\python.exe"))) {
        & $pythonExe -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Falha ao criar ambiente virtual Python." }
    }
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    & $venvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar pip." }
    & $venvPython -m pip install --disable-pip-version-check $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependências do GoData." }

    Write-Step "Gerando configuração segura"
    $envFile = Join-Path $InstallDir ".env"
    $existingKey = $null
    if ((Test-Path -LiteralPath $envFile) -and -not $RotateApiKey) {
        $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^GODATA_API_KEY=' } | Select-Object -First 1
        if ($line) { $existingKey = $line.Substring("GODATA_API_KEY=".Length).Trim() }
    }
    $apiKey = if ($existingKey) { $existingKey } else { New-ApiKey }
    @(
        "GODATA_API_KEY=$apiKey",
        "GODATA_ODBC_DRIVER=ODBC Driver 18 for SQL Server",
        "GODATA_ENCRYPT=true",
        "GODATA_TRUST_SERVER_CERTIFICATE=true",
        "GODATA_CONNECTION_TIMEOUT_SECONDS=2048",
        "GODATA_QUERY_TIMEOUT_SECONDS=0",
        "GODATA_MAX_ROWS=1500000",
        "GODATA_MAX_QUERY_LENGTH=100000",
        "GODATA_MAX_CONCURRENT_QUERIES=10"
    ) | Set-Content -LiteralPath $envFile -Encoding ASCII

    $launcher = Join-Path $InstallDir "scripts\start-godata.py"
    if (-not (Test-Path -LiteralPath $launcher)) { throw "Launcher não encontrado: $launcher" }
    $startupMethod = Register-GoDataStartup $venvPython $launcher

    Write-Step "Iniciando GoData"
    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Host "GoData instalado com sucesso" -ForegroundColor Green
    Write-Host "Pasta:    $InstallDir"
    Write-Host "Startup:  $startupMethod"
    Write-Host "API Key:  $apiKey" -ForegroundColor Yellow
    Write-Host "A URL do túnel aparecerá na janela do GoData." -ForegroundColor Cyan
    Write-Host "============================================================"

    try {
        Start-Process -FilePath $venvPython -ArgumentList ('"{0}"' -f $launcher) -WorkingDirectory $InstallDir
    } catch {
        Write-Host "O Windows bloqueou Start-Process; iniciando na janela atual." -ForegroundColor Yellow
        & $venvPython $launcher
    }
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
