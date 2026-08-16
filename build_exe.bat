@echo off
rem Builds DubMaker.exe + DubStage.exe into dist\DubSuite (one shared folder).
rem Requires: the tools\ffmpeg.exe that Setup.bat / the app already put in place.
cd /d "%~dp0"
set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY ( echo Python not found. & pause & exit /b 1 )

if not exist "tools\ffmpeg.exe" (
  echo [!] tools\ffmpeg.exe is missing - run Setup.bat once so ffmpeg is present,
  echo     then run this again.
  pause & exit /b 1
)

echo Installing PyInstaller ...
%PY% -m pip install --upgrade --quiet pyinstaller || ( echo pip failed & pause & exit /b 1 )

echo Building ...
%PY% -m PyInstaller --noconfirm --clean --distpath dist --workpath build_pyi dubsuite.spec || (
  echo Build failed. & pause & exit /b 1 )

echo.
echo Done.  Your apps are in:  dist\DubSuite\
echo   DubMaker.exe  - make dub packs
echo   DubStage.exe  - record and host rooms
pause
