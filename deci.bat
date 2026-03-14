echo [6/7] Installing Gaussian_Decimator support...
set "DECIMATOR_DIR=%SCRIPT_DIR%Gaussian_Decimator"
if exist "%DECIMATOR_DIR%\.git" (
    echo       Existing Gaussian_Decimator checkout found. Updating...
    pushd "%DECIMATOR_DIR%"
    git pull --ff-only
    if errorlevel 1 (
        echo       WARNING: Could not update Gaussian_Decimator. Using existing checkout.
    )
    popd
)
if not exist "%DECIMATOR_DIR%\decimate.py" (
    echo       Cloning Gaussian_Decimator repository...
    git clone https://github.com/feel3x/Gaussian_Decimator.git "%DECIMATOR_DIR%"
    if errorlevel 1 (
        echo       WARNING: Could not clone Gaussian_Decimator. The decimation feature will stay unavailable.
        goto :after_decimator
    )
)
if exist "%DECIMATOR_DIR%\decimate.py" (
    echo       Installing Gaussian_Decimator Python dependencies...
    call "%CONDA_CMD%" run -n sharp pip install numpy==2.3.2 plyfile==1.1.2 tqdm==4.67.0
    if errorlevel 1 (
        echo       WARNING: Could not install Gaussian_Decimator base dependencies.
        echo                The decimation feature may not work until these packages are installed manually.
    )
    call "%CONDA_CMD%" run -n sharp pip install torch_scatter
    if errorlevel 1 (
        echo       WARNING: Could not install torch_scatter automatically.
        echo                Gaussian decimation will stay unavailable until torch_scatter is installed.
    ) else (
        echo       OK - Gaussian_Decimator support installed.
    )
)