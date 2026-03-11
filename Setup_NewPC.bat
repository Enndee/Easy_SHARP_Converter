@echo off
setlocal EnableDelayedExpansion

echo =====================================================
echo  Easy SHARP Converter  --  New PC Setup
echo =====================================================
echo.
echo This script will:
echo   1. Locate Anaconda or Miniconda reliably on Windows
echo   2. Create the 'sharp' conda environment (Python 3.13)
echo   3. Install PyTorch with CUDA 12.8 BEFORE ml-sharp
echo   4. Install Apple's ml-sharp package from GitHub
echo   5. Create a Send To shortcut for Easy SHARP Converter
echo.
echo Press any key to continue, or Ctrl+C to cancel.
pause >nul

set "SCRIPT_DIR=%~dp0"
set "CONDA_BASE="
set "CONDA_CMD="

echo.
echo [1/5] Locating conda...
for %%B in (
    "%USERPROFILE%\anaconda3"
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\Anaconda3"
    "%USERPROFILE%\Miniconda3"
    "%LOCALAPPDATA%\anaconda3"
    "%LOCALAPPDATA%\miniconda3"
    "%LOCALAPPDATA%\Anaconda3"
    "%LOCALAPPDATA%\Miniconda3"
    "%APPDATA%\anaconda3"
    "%APPDATA%\miniconda3"
    "%ProgramData%\anaconda3"
    "%ProgramData%\miniconda3"
    "%ProgramData%\Anaconda3"
    "%ProgramData%\Miniconda3"
    "C:\anaconda3"
    "C:\miniconda3"
    "D:\anaconda3"
    "D:\miniconda3"
) do (
    if exist "%%~B\condabin\conda.bat" (
        set "CONDA_BASE=%%~B"
        set "CONDA_CMD=%%~B\condabin\conda.bat"
        goto :found_conda
    )
)

for /f "usebackq delims=" %%B in (`conda info --base 2^>nul`) do (
    if exist "%%~B\condabin\conda.bat" (
        set "CONDA_BASE=%%~B"
        set "CONDA_CMD=%%~B\condabin\conda.bat"
        goto :found_conda
    )
)

echo.
echo ERROR: Could not find Anaconda or Miniconda.
echo.
echo Please install one of them first:
echo   https://www.anaconda.com/download
echo.
echo After installing, open a fresh terminal and run this script again.
pause
exit /b 1

:found_conda
echo       OK - conda found at %CONDA_BASE%

echo.
echo [2/5] Checking for git...
where git >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: git not found in PATH.
    echo.
    echo Please install Git for Windows:
    echo   https://git-scm.com/download/win
    echo.
    echo After installing, open a fresh terminal and run this script again.
    pause
    exit /b 1
)
echo       OK - git found.

echo.
echo [3/5] Creating conda environment 'sharp' with Python 3.13...
call "%CONDA_CMD%" env list | findstr /R /C:"^[* ]*sharp[ ]" >nul 2>&1
if not errorlevel 1 (
    echo       Environment 'sharp' already exists -- skipping creation.
    goto :install_torch
)
call "%CONDA_CMD%" create -n sharp python=3.13 -y
if errorlevel 1 (
    echo ERROR: Failed to create conda environment.
    pause
    exit /b 1
)

:install_torch
echo.
echo [4/5] Installing PyTorch with CUDA 12.8 support...
echo       IMPORTANT: This must happen before ml-sharp to avoid CPU-only torch.
echo       This may take a few minutes...
call "%CONDA_CMD%" run -n sharp pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 (
    echo.
    echo WARNING: PyTorch CUDA install failed.
    echo          Trying CPU-only version as fallback...
    call "%CONDA_CMD%" run -n sharp pip install torch torchvision
    if errorlevel 1 (
        echo ERROR: Could not install PyTorch at all.
        pause
        exit /b 1
    )
    echo       WARNING: CPU-only PyTorch installed. Conversions will be slow.
) else (
    echo       OK - PyTorch with CUDA installed.
)

echo.
echo [5/5] Installing ml-sharp from GitHub...
echo       Step 1: Cloning repository (this may take 1-2 minutes, please wait)...
echo       Step 2: Installing dependencies (another 1-2 minutes)...
echo       If it appears stuck after the git clone line, it is still working.
call "%CONDA_CMD%" run -n sharp pip install "git+https://github.com/apple/ml-sharp.git"
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install ml-sharp.
    echo        Make sure git is installed and you have internet access.
    pause
    exit /b 1
)
echo       OK - ml-sharp installed.

echo.
echo [Bonus] Creating Send To shortcut...
set "EXE_PATH=%SCRIPT_DIR%Easy_SHARP_Converter.exe"
if not exist "%EXE_PATH%" (
    echo       WARNING: Easy_SHARP_Converter.exe was not found next to this script.
    echo       The shortcut will target Launch_Easy_SHARP.bat instead.
    set "EXE_PATH=%SCRIPT_DIR%Launch_Easy_SHARP.bat"
)
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$lnk = $ws.CreateShortcut([Environment]::GetFolderPath('SendTo') + '\Easy SHARP Converter.lnk'); " ^
    "$lnk.TargetPath = '%EXE_PATH:\=\\%'; " ^
    "$lnk.WorkingDirectory = '%SCRIPT_DIR:\=\\%'; " ^
    "$lnk.Save()"
if errorlevel 1 (
    echo       WARNING: Could not create Send To shortcut automatically.
    echo       You can create it manually and place it in:
    echo       %%APPDATA%%\Microsoft\Windows\SendTo\
) else (
    echo       OK - shortcut created.
    echo       Right-click any JPEG/PNG -^> Send To -^> Easy SHARP Converter
)

echo.
echo =====================================================
echo  Setup complete!
echo.
echo  FIRST RUN NOTE:
echo    On the first conversion, SHARP will download its model
echo    weights (~500MB) from Apple's servers automatically.
echo    This only happens once.
echo =====================================================
pause
