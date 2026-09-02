@echo off
setlocal enabledelayedexpansion
set "args="
:parse
if "%~1"=="" goto run
set "arg=%~1"
if "%arg%"=="-snld" (
    set "args=!args! -sni"
) else (
    set "args=!args! %~1"
)
shift
goto parse
:run
"C:\Users\Jamali\Desktop\accountant\desktop\node_modules\.pnpm\7zip-bin@5.2.0\node_modules\7zip-bin\win\x64\7za.exe" !args!
