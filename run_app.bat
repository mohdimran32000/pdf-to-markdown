@echo off
echo Starting Gemini 3 OCR Web App...
echo Open http://localhost:8000 in your browser.
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
pause
