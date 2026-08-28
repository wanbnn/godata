@echo off
setlocal
set "INSTALLER=%TEMP%\godata-install-%RANDOM%.ps1"
set "INSTALLER_URL=https://raw.githubusercontent.com/wanbnn/godata/main/install.ps1"

where curl.exe >nul 2>&1
if not errorlevel 1 (
  curl.exe -fL "%INSTALLER_URL%" -o "%INSTALLER%"
) else (
  where wget.exe >nul 2>&1
  if not errorlevel 1 (
    wget.exe -q "%INSTALLER_URL%" -O "%INSTALLER%"
  ) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('%INSTALLER_URL%','%INSTALLER%')"
  )
)

if not exist "%INSTALLER%" (
  echo Falha ao baixar o instalador do GoData.
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%"
set "RESULT=%ERRORLEVEL%"
del /q "%INSTALLER%" >nul 2>&1
exit /b %RESULT%
