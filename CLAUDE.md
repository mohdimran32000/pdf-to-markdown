# PDF to Markdown OCR Web App

## Objective
Web application that converts PDF files to Markdown using Google Gemini 3 Flash.
Both single-file and batch (up to 70+ files) processing, producing one stitched combined
Markdown file as output.

## Architecture
- **Backend**: FastAPI + Uvicorn (`app/main.py`)
- **Frontend**: Vanilla JS + HTML/CSS (`app/static/`)
- **OCR engine**: Google Gemini 3 Flash Preview via `google-genai` SDK
- **PDF handling**: `pypdf` for page-level splitting before API upload
- **Observability**: `/dashboard.html`, `/status`, `/logs` endpoints, file logging to `logs/ocr.log`

## How to Run

```bash
export GEMINI_API_KEY="your-key"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 (main app) or http://localhost:8000/dashboard.html (observability)

## Environment Variables
| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | -- | Google Gemini API key (free tier works) |
| `GEMINI_MODEL` | No | `gemini-3-flash-preview` | Model ID override |

## Features
- [x] Single PDF upload -> Markdown (split view: PDF + rendered Markdown)
- [x] Batch upload (multi-select) -> one stitched `combined_output.md`
- [x] Smart PDF splitting: 15 pages/chunk to avoid silent page skipping
- [x] SSE streaming progress with real-time stages and elapsed time
- [x] Batch progress UI: per-file status, progress bar, live counter, retry failed
- [x] Retry logic with backoff on rate limits, timeouts, and server errors
- [x] Observability dashboard: `/dashboard.html` with live job status, chunk timing, logs
- [x] Persistent logging to `logs/ocr.log` with request IDs

## Gemini API Details
- **Model**: `gemini-3-flash-preview` (best free-tier OCR model, #2 on OCR Arena ELO 1685)
- **Generation config**: thinking disabled, temperature=0, 65K max output tokens
- **Free tier limits**: ~10-15 RPM, project-dependent RPD (check AI Studio)
- **Context window**: 1M tokens input, 64K tokens output
- **Cost**: Free on free tier

## Key Design Decisions

### Why Gemini 3 Flash?
After testing PaddleOCR PP-StructureV3, LlamaParse, and Gemini, Gemini 3 Flash
produced the best OCR results overall. It ranks #2 on OCR Arena benchmarks and
outperforms Gemini 2.5 Pro in head-to-head OCR comparisons (53.3% win rate).

### Why split PDFs into 15-page chunks?
Gemini silently skips pages in large PDF inputs. Splitting into 15-page chunks
ensures every page is processed. The markdown output is stitched back together.

### Why not Gemini 3.1 Pro?
Gemini 3.1 Pro is paid-only ($2/$12 per 1M tokens). No free tier.
Gemini 3 Flash is the best free-tier model for OCR.

## Known Issues & Fixes

### Unicode crash in Windows console -- FIXED
- **Root cause**: Unicode in `print()` crashes Windows `charmap` codec
- **Fix**: Use `logging` module with UTF-8 file handler, ASCII-safe console output

### Server freeze under large batches -- FIXED
- **Root cause**: Running with `--reload` adds a file watcher that consumes threads
- **Fix**: Always start without `--reload`

## Other Files (Not Part of Web App)
- `vertex_ocr_drive.py` -- standalone script for Google Drive batch workflow
- `verify_drive_output.py` -- read-only Drive verification utility
