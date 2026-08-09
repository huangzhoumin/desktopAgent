@echo off
setlocal EnableExtensions

rem Start Chrome with an isolated profile + CDP debug port (mode B attach).
rem Independent of your daily Chrome profile / login state.

set "PORT=9222"
set "HOST=127.0.0.1"
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
echo   CDP        : http://%HOST%:%PORT%
echo.
echo Next: py -m desktop_agent doctor
echo       py -m desktop_agent browser-probe
echo.

start "" "%CHROME%" ^
  --remote-debugging-port=%PORT% ^
  --remote-debugging-address=%HOST% ^
  --user-data-dir="%PROFILE%" ^
  --no-first-run ^
  --no-default-browser-check

endlocal
exit /b 0
