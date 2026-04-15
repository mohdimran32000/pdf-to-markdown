@echo off
echo ============================================
echo   Gemini 3 OCR Web App
echo ============================================
echo.

REM Load API key from .env file if it exists
if exist .env (
    for /f "tokens=1,2 delims==" %%a in (.env) do (
        if "%%a"=="GEMINI_API_KEY" set GEMINI_API_KEY=%%b
    )
    echo   API key loaded from .env
) else (
    echo   WARNING: No .env file found.
    echo   Create a .env file with: GEMINI_API_KEY=your-key
)
echo.
echo Starting server...
echo   App:        http://localhost:8000
echo   Dashboard:  http://localhost:8000/dashboard.html
echo.
echo Press Ctrl+C to stop.
echo ============================================
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
