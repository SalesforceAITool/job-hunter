@echo off
REM Job Hunter one-time setup: finds Python, installs reportlab if missing, creates the Desktop shortcut, starts the app.
REM Python search order: py -3, python (not the Microsoft Store stub), Anaconda / Miniconda folders, python.org folders.
setlocal EnableExtensions
cd /d "%~dp0"
set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"
set "BASEPATH=%PATH%"
set "PYEXE="

echo === Job Hunter setup ===
echo Looking for Python 3.9 or newer...

for /f "usebackq delims=" %%i in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do call :try_exe "%%i"
for /f "usebackq delims=" %%i in (`python -c "import sys; print(sys.executable)" 2^>nul`) do call :try_exe "%%i"
call :try_exe "%USERPROFILE%\anaconda3\python.exe"
call :try_exe "%USERPROFILE%\miniconda3\python.exe"
call :try_exe "%LOCALAPPDATA%\anaconda3\python.exe"
call :try_exe "%LOCALAPPDATA%\miniconda3\python.exe"
call :try_exe "%ProgramData%\anaconda3\python.exe"
call :try_exe "%ProgramData%\miniconda3\python.exe"
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try_exe "%%d\python.exe"
for /d %%d in ("%ProgramFiles%\Python3*") do call :try_exe "%%d\python.exe"
if not defined PYEXE goto :no_python

echo Using Python: "%PYEXE%"
"%PYEXE%" --version

if not exist "%APPDIR%\data" mkdir "%APPDIR%\data"
REM The launcher (launch_windows.vbs) starts pythonw.exe from this folder without searching again.
> "%APPDIR%\data\python_path.txt" echo %PYEXE%

echo.
"%PYEXE%" -c "import reportlab" >nul 2>&1
if not errorlevel 1 goto :have_reportlab
echo Installing reportlab - the resume PDF builder...
"%PYEXE%" -m pip install --user reportlab
"%PYEXE%" -c "import reportlab" >nul 2>&1
if errorlevel 1 goto :pip_failed
:have_reportlab
echo reportlab is installed.

echo.
echo Creating Desktop shortcut "Job Hunter"...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Startup'))) {" ^
  "  $l = $s.CreateShortcut((Join-Path $dir 'Job Hunter.lnk'));" ^
  "  $l.TargetPath = (Join-Path $env:WINDIR 'System32\wscript.exe');" ^
  "  $l.Arguments = [char]34 + $env:APPDIR + '\launch_windows.vbs' + [char]34;" ^
  "  $l.WorkingDirectory = $env:APPDIR;" ^
  "  $l.IconLocation = (Join-Path $env:WINDIR 'System32\imageres.dll') + ',109';" ^
  "  $l.Description = 'Job Hunter - Salesforce job outreach';" ^
  "  $l.Save() }"
if errorlevel 1 goto :no_shortcut
echo Shortcut created on your Desktop, in the Start menu and in Startup (opens when Windows starts).
goto :start_app
:no_shortcut
echo Could not create the shortcut. You can start the app by double-clicking launch_windows.vbs.

:start_app
echo.
echo Starting Job Hunter...
start "" wscript.exe "%APPDIR%\launch_windows.vbs"
echo Done. Next time just double-click "Job Hunter" on your Desktop.
echo First steps: open Settings, enter your Gmail App Password, click "Test email connection", then turn off Dry run.
timeout /t 8 >nul
endlocal
exit /b 0

:no_python
echo.
echo Python 3.9 or newer was not found on this PC.
echo Checked: py -3, python, Anaconda / Miniconda folders and %LOCALAPPDATA%\Programs\Python.
echo Download Python from https://www.python.org/downloads/windows/
echo During setup tick "Add python.exe to PATH", then run install_windows.bat again.
pause
exit /b 1

:pip_failed
echo.
echo Could not install reportlab. Check your internet connection and run install_windows.bat again.
echo Or run this yourself:  "%PYEXE%" -m pip install --user reportlab
pause
exit /b 1

REM ---------------------------------------------------------------------------------------------
REM :try_exe "<path to python.exe>" - keeps the first interpreter that is Python 3.9+ and can import
REM ssl and sqlite3. The Microsoft Store stub (WindowsApps) is rejected. Anaconda's DLL folders
REM (Library\bin) go on PATH first, so a non-activated Anaconda can load ssl.
:try_exe
if defined PYEXE goto :eof
if "%~1"=="" goto :eof
if not exist "%~1" goto :eof
echo "%~1" | find /i "WindowsApps" >nul && goto :eof
set "PATH=%~dp1;%~dp1Library\bin;%~dp1Scripts;%BASEPATH%"
"%~1" -c "import sys, ssl, sqlite3; assert sys.version_info >= (3, 9)" >nul 2>&1
if errorlevel 1 goto :try_exe_bad
set "PYEXE=%~1"
goto :eof
:try_exe_bad
set "PATH=%BASEPATH%"
goto :eof
