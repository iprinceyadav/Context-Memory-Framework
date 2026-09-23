@echo off
setlocal

echo ==========================================
echo Context Memory Framework (CMF)
echo ==========================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to your PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b 1
)

:: Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Virtual environment not found.
    echo [INFO] Creating a new virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    
    echo [INFO] Activating virtual environment...
    call venv\Scripts\activate.bat
    
    echo [INFO] Installing dependencies from requirements.txt...
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo [INFO] Setup complete!
) else (
    echo [INFO] Activating existing virtual environment...
    call venv\Scripts\activate.bat
)

:: Check if .env file exists, if not create one from .env.example
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] Creating .env file from .env.example...
        copy .env.example .env >nul
        echo [WARN] Please ensure you open the .env file and add your LLM_API_KEY (e.g. Gemini key) before querying.
    )
)

echo.
echo ==========================================
echo Starting FastAPI Server...
echo The application will be available at:
echo http://127.0.0.1:8000
echo ==========================================
echo.

:: Run the FastAPI application using uvicorn
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

pause
