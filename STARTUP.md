# How to Start the App

## Prerequisites

1. **Python 3.10+** installed
2. **Dependencies** installed:
   ```bash
   pip install -r requirements.txt
   ```
3. **Gemini API key** — get one free at https://aistudio.google.com/apikey

## Quick Start (Windows)

### Option 1: Double-click `run_app.bat` (easiest)

The API key is already saved in the `.env` file. Just:

1. Double-click **`run_app.bat`**
2. Open http://localhost:8000

That's it. The batch file loads the key from `.env` automatically.

### Option 2: Command line

```cmd
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

(The app also loads `.env` automatically when started this way.)

## Changing the API Key

Edit the `.env` file in the project root:

```
GEMINI_API_KEY=your-new-key-here
```

## Quick Start (Mac/Linux)

```bash
export GEMINI_API_KEY="your-key-here"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## URLs

| URL | What it does |
|-----|-------------|
| http://localhost:8000 | Main app — upload PDFs, get Markdown |
| http://localhost:8000/dashboard.html | Observability dashboard — job status, logs |

## Stopping the Server

Press **Ctrl+C** in the terminal.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `GEMINI_API_KEY not set` | Set the env variable (see above) |
| Port 8000 already in use | Kill the old process or use a different port: `--port 8001` |
| Server freezes on large batches | Make sure you're NOT using `--reload` flag |
| Unicode crash in terminal | Already fixed — app uses logging module with UTF-8 file handler |
