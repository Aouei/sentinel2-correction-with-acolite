@echo off
rem Runs the Sentinel-2 correction ETL for every day of a year over the Formosa ROI (tile T29SPA).
rem
rem Every date runs in its own process, so a failure on one date never stops or affects the others.
rem It is safe to re-run: dates that are already corrected finish in a few seconds without
rem exporting anything, and dates that failed are retried.
rem
rem Optional environment variables:
rem   YEAR     year to process (default 2018)
rem   LIMIT    only process the first N dates of the year
rem   DRY_RUN  set to 1 to print the commands instead of running them
rem
rem Exit code: 0 when every date finished OK, 1 when at least one date failed,
rem 2 when a prerequisite is missing, 3 when Earth Engine is not available.

setlocal EnableDelayedExpansion
cd /d "%~dp0"

if not defined YEAR set "YEAR=2018"
set "TILE=T29SPA"
set "ROI=roi.json"
set "SETTINGS=settings.json"
set "ACOLITE_VERSION=20231023.0"
set "ACOLITE_PATH=data\acolite-20231023.0"
set "EE_PROJECT=ee-sergiohercar1"
set "LOG_DIR=logs\%TILE%_%YEAR%"

rem --- Preflight: fail once here instead of 365 times in the loop.
if not "%DRY_RUN%"=="1" (
    where uv >nul 2>&1 || (echo ERROR: uv is not installed or not in PATH & exit /b 2)
    for %%F in ("%ROI%" "%SETTINGS%") do if not exist %%F (echo ERROR: missing file %%~F & exit /b 2)
    if not exist "%ACOLITE_PATH%\acolite" (echo ERROR: ACOLITE folder not found: %ACOLITE_PATH% & exit /b 2)
    call uv run python -c "import ee; ee.Initialize(project='%EE_PROJECT%')" >nul 2>&1
    if errorlevel 1 (echo ERROR: Earth Engine is not available. Log in once with: uv run earthengine authenticate & exit /b 3)
    if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
    if exist "%LOG_DIR%\failed.txt" del "%LOG_DIR%\failed.txt"
)

set /a OK=0
set /a FAILED=0
set /a N=0

for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "$d=[datetime]'%YEAR%-01-01'; $end=[datetime]'%YEAR%-12-31'; while($d -le $end){$d.ToString('yyyy-MM-dd'); $d=$d.AddDays(1)}"`) do (
    set /a N+=1
    set "RUN=1"
    if defined LIMIT if !N! gtr !LIMIT! set "RUN=0"
    if "!RUN!"=="1" (
        set "CMD=uv run python main.py --date %%D --tile %TILE% --roi %ROI% --settings %SETTINGS% --acolite-version %ACOLITE_VERSION% --acolite-path %ACOLITE_PATH%"
        if "%DRY_RUN%"=="1" (
            echo !CMD!
        ) else (
            call !CMD! > "%LOG_DIR%\%%D.log" 2>&1
            set "CODE=!ERRORLEVEL!"
            echo [!TIME!] %%D exit !CODE!
            if "!CODE!"=="0" (
                set /a OK+=1
            ) else (
                set /a FAILED+=1
                >> "%LOG_DIR%\failed.txt" echo %%D !CODE!
            )
        )
    )
)

if "%DRY_RUN%"=="1" exit /b 0

echo.
echo Finished: !OK! ok, !FAILED! failed. Logs: %LOG_DIR%
if !FAILED! gtr 0 (
    echo Failed dates and exit codes: %LOG_DIR%\failed.txt
    exit /b 1
)
exit /b 0
