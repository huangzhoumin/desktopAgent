@echo off
setlocal EnableExtensions

rem Start Chrome with an isolated profile + CDP debug port (mode B attach).
rem Chrome 136+ silently ignores --remote-debugging-port on the default profile,
rem so this launcher always uses a dedicated --user-data-dir.

set "PORT=9222"
set "PROFILE=%LOCALAPPDATA%\DesktopAgent\browser-debug-profile\chrome"

set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
  set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
) else if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
  set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
)

if not defined CHROME (
  echo [ERROR] Cannot find chrome.exe
  pause
  exit /b 1
)

if not exist "%PROFILE%" mkdir "%PROFILE%"

echo Starting isolated debug Chrome
echo   Executable : %CHROME%
echo   Profile    : %PROFILE%
echo   CDP        : http://127.0.0.1:%PORT%
echo.

rem Put all args on one line. Multi-line caret continuation can drop flags under start.
rem Do NOT pass --remote-debugging-address; Chrome listens on localhost by default.
start "Chrome Debug CDP" /D "%~dp0" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check --new-window "about:blank"

echo Waiting for CDP endpoint...
set /a _tries=0
:wait_cdp
set /a _tries+=1
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/json/version' -UseBasicParsing -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %ERRORLEVEL%==0 (
  echo CDP is ready: http://127.0.0.1:%PORT%
  echo Next: py -m desktop_agent doctor
  echo       py -m desktop_agent browser-probe
  endlocal
  exit /b 0
)
if %_tries% GEQ 20 goto cdp_fail
timeout /t 1 /nobreak >nul
goto wait_cdp

:cdp_fail
echo [ERROR] Chrome started but CDP is not reachable on port %PORT%.
echo   1^) Close ALL Chrome windows and retry this script.
echo   2^) Confirm no other process owns port %PORT%.
echo   3^) Open http://127.0.0.1:%PORT%/json/version in a browser tab.
pause
endlocal
exit /b 2
