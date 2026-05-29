@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "PROJECT_DIR=%CD%"
set "SPEC_FILE=%PROJECT_DIR%\launcher_small.spec"
set "DIST_DIR=%PROJECT_DIR%\dist\ProgTrack_small"
set "LOCAL_ENV=%PROJECT_DIR%\.build_env_py312"
set "BUILD_TEMP=%PROJECT_DIR%\.build_tmp"
set "PYTHON_EXE="

if not exist "%BUILD_TEMP%" mkdir "%BUILD_TEMP%"
set "TMP=%BUILD_TEMP%"
set "TEMP=%BUILD_TEMP%"
set "PIP_NO_INDEX="
set "PIP_INDEX_URL=https://pypi.org/simple"

echo.
echo ProgTrack small launcher build
echo ==============================
echo Project: %PROJECT_DIR%
echo Build environment: local pip venv only

if not exist "%SPEC_FILE%" (
    echo ERROR: launcher_small.spec was not found.
    exit /b 1
)

if exist "%LOCAL_ENV%\Scripts\python.exe" set "PYTHON_EXE=%LOCAL_ENV%\Scripts\python.exe"

if not defined PYTHON_EXE (
    echo No existing build environment found. Creating local venv: %LOCAL_ENV%
    set "BOOTSTRAP_PY="
    py -3.10 -c "import sys" >nul 2>nul && set "BOOTSTRAP_PY=py -3.10"
    if not defined BOOTSTRAP_PY py -3 -c "import sys" >nul 2>nul && set "BOOTSTRAP_PY=py -3"
    if not defined BOOTSTRAP_PY (
        if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
            "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>nul && set "BOOTSTRAP_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
        )
    )
    if not defined BOOTSTRAP_PY python -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul && set "BOOTSTRAP_PY=python"
    if not defined BOOTSTRAP_PY (
        echo ERROR: No usable Python 3.10, 3.11, or 3.12 found. Python 3.13 is not used for this Qt launcher build.
        exit /b 1
    )
    !BOOTSTRAP_PY! -m venv "%LOCAL_ENV%"
    if errorlevel 1 (
        echo Standard venv creation did not provide pip. Retrying with explicit pip bootstrap...
        !BOOTSTRAP_PY! -m venv --without-pip "%LOCAL_ENV%"
        if errorlevel 1 exit /b 1
        !BOOTSTRAP_PY! -m pip --python "%LOCAL_ENV%\Scripts\python.exe" install --upgrade pip setuptools wheel
        if errorlevel 1 exit /b 1
    )
    set "PYTHON_EXE=%LOCAL_ENV%\Scripts\python.exe"
)

echo Using Python: %PYTHON_EXE%
"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)"
if errorlevel 1 (
    echo ERROR: Local build environment must use Python 3.10, 3.11, or 3.12. Remove %LOCAL_ENV% and rebuild.
    exit /b 1
)
"%PYTHON_EXE%" -m ensurepip --upgrade >nul 2>nul
"%PYTHON_EXE%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo ERROR: Local build environment has no working pip.
    exit /b 1
)

"%PYTHON_EXE%" -c "import importlib.util, sys; mods=['PyInstaller','PyQt6','matplotlib','numpy','pandas','scipy','openpyxl','reportlab','PIL','numexpr']; missing=[m for m in mods if importlib.util.find_spec(m) is None]; print('Missing packages: '+', '.join(missing) if missing else 'Required packages already importable.'); sys.exit(1 if missing else 0)"
if errorlevel 1 (
    echo Installing required build/runtime packages...
    "%PYTHON_EXE%" -m pip install --upgrade pip setuptools wheel
    if errorlevel 1 exit /b 1
    "%PYTHON_EXE%" -m pip install --upgrade ^
        pyinstaller==6.14.1 ^
        PyQt6==6.7.1 ^
        matplotlib==3.10.0 ^
        numpy==2.2.5 ^
        pandas==2.2.3 ^
        scipy==1.15.3 ^
        openpyxl==3.1.5 ^
        reportlab==4.4.10 ^
        Pillow==11.1.0 ^
        numexpr==2.10.2 ^
        pyqtgraph
    if errorlevel 1 exit /b 1
)

"%PYTHON_EXE%" -c "import PyQt6.QtMultimedia, PyQt6.QtMultimediaWidgets"
if errorlevel 1 (
    echo ERROR: PyQt6 multimedia modules are still missing after package installation.
    exit /b 1
)

echo.
echo Building PyInstaller runtime...
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean "%SPEC_FILE%"
if errorlevel 1 exit /b 1

echo.
echo Build complete:
echo %DIST_DIR%\Launcher.exe
echo.
echo This builds only the launcher runtime. Place ProgTrack.v.*.py, Plugins, icons,
echo lang, manual, Licensing, config/data files, and info_*.json beside Launcher.exe
echo before testing the application.

endlocal
