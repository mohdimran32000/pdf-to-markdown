from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Form
from fastapi.responses import PlainTextResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import json
import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone
import io
import os
import re
import asyncio
import pypdf
from dotenv import load_dotenv

load_dotenv()  # Load .env file from project root

# ---------------------------------------------------------------------------
# Logging setup: file + console, with timestamps
# ---------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)

LOG_FILE = "logs/ocr.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

logger = logging.getLogger("ocr")
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# ---------------------------------------------------------------------------
# In-memory request tracker (last 50 jobs)
# ---------------------------------------------------------------------------
request_history = deque(maxlen=50)
current_job = None

def new_job(request_id: str, filename: str, total_pages: int, total_chunks: int) -> dict:
    return {
        "request_id": request_id,
        "filename": filename,
        "total_pages": total_pages,
        "total_chunks": total_chunks,
        "status": "processing",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "chunks": [],
        "error": None,
        # Page-level tracking
        "pages_expected": list(range(1, total_pages + 1)),
        "pages_received": [],
        "pages_missing": [],
        "pages_empty": [],
        "pages_truncated": [],
        "pages_retried": [],
    }

def finish_job(job: dict, status: str, error: str = None):
    job["status"] = status
    job["completed_at"] = datetime.now(timezone.utc).isoformat()
    # Compute final missing pages
    received_set = set(job["pages_received"])
    expected_set = set(job["pages_expected"])
    job["pages_missing"] = sorted(expected_set - received_set)
    job["error"] = error
    request_history.appendleft(job)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="PDF OCR to Markdown")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Gemini configuration
# ---------------------------------------------------------------------------
MODEL_ID = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")
ALLOWED_MODELS = {"gemini-3.1-pro-preview", "gemini-3-flash-preview"}
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PAGES_PER_CHUNK = 15  # Pages per API request

# ---------------------------------------------------------------------------
# Initialize Gemini client
# ---------------------------------------------------------------------------
from google import genai
from google.genai import types

GENERATION_CONFIG = types.GenerateContentConfig(
    max_output_tokens=65536,
)

if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY environment variable is not set!")
else:
    logger.info("Gemini API key loaded (ends ...%s)", GEMINI_API_KEY[-4:])

client = genai.Client(api_key=GEMINI_API_KEY)
logger.info("Gemini client initialized for model: %s", MODEL_ID)

# ---------------------------------------------------------------------------
# PDF utilities
# ---------------------------------------------------------------------------

def get_pdf_page_count(content: bytes) -> int:
    reader = pypdf.PdfReader(io.BytesIO(content))
    return len(reader.pages)

def split_pdf_pages(content: bytes, start: int, end: int) -> bytes:
    """Extract pages [start, end) (0-indexed) from a PDF and return as bytes."""
    reader = pypdf.PdfReader(io.BytesIO(content))
    writer = pypdf.PdfWriter()
    for i in range(start, min(end, len(reader.pages))):
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()

def make_page_chunks(total_pages: int, chunk_size: int) -> list[dict]:
    """Return list of {start_page, end_page} dicts (1-indexed, inclusive)."""
    chunks = []
    for start_0 in range(0, total_pages, chunk_size):
        end_0 = min(start_0 + chunk_size, total_pages)
        chunks.append({
            "start_page": start_0 + 1,
            "end_page": end_0,
            "start_idx": start_0,
            "end_idx": end_0,
        })
    return chunks

# ---------------------------------------------------------------------------
# Page validation: check which <!-- Page X --> markers are present
# ---------------------------------------------------------------------------

def validate_pages(markdown: str, expected_start: int, expected_end: int) -> dict:
    """
    Parse markdown for <!-- Page X --> markers and return validation result.
    Returns {found: [int], missing: [int], expected: [int]}
    """
    expected = list(range(expected_start, expected_end + 1))
    # Match <!-- Page 5 --> style markers (flexible whitespace)
    found_markers = re.findall(r'<!--\s*Page\s+(\d+)\s*-->', markdown)
    found = sorted(set(int(m) for m in found_markers))
    missing = sorted(set(expected) - set(found))
    return {"found": found, "missing": missing, "expected": expected}

# ---------------------------------------------------------------------------
# OCR prompt
# ---------------------------------------------------------------------------

def build_ocr_prompt(start_page: int, end_page: int, total_pages: int) -> str:
    return f"""<system_instruction>
You are an expert, highly precise Document OCR Engine. Your objective is to extract text and structure from this document.
</system_instruction>

<formatting_rules>
1. Page Tracking: Start EVERY page with: <!-- Page X -->
   Pages in this chunk: {", ".join(str(p) for p in range(start_page, end_page + 1))}
2. Text Content: Preserve ALL text exactly as written. Use standard markdown for headings (#, ##) and lists. No summaries, no omissions.
3. Equations: Use strict LaTeX ($...$) for mathematical formulas. You MUST use proper macros (e.g., `\tan`, `\cos^{-1}`) instead of plain text for math.
4. Images/Diagrams: Output [Image: brief summary of what is shown].
5. TABLES (CRITICAL): 
   - You MUST output ALL tables using strictly well-formed HTML tags (<table>, <tr>, <th>, <td>). 
   - You MUST use `colspan` and `rowspan` to accurately recreate merged cells.
   - You are STRICTLY FORBIDDEN from generating piped markdown tables (e.g., `| Header | Header |`).
   - If you detect tabular data, immediately open a <table> tag.
</formatting_rules>

<output_format>
Output ONLY the requested content following the rules above. Do not include any conversational preamble.
</output_format>"""

# ---------------------------------------------------------------------------
# Gemini API client with retry
# ---------------------------------------------------------------------------

async def call_gemini_with_retry(contents, model_id=None, request_id="",
                                  chunk_label="", max_retries=5, on_event=None):
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("[%s] Gemini API call START %s (attempt %d/%d)",
                       request_id, chunk_label, attempt, max_retries)
            t0 = time.time()

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=model_id or MODEL_ID,
                    contents=contents,
                    config=GENERATION_CONFIG,
                ),
                timeout=600.0
            )

            duration = time.time() - t0

            # Handle safety-filtered / blocked responses
            if not response.candidates:
                logger.error("[%s] Gemini returned NO candidates for %s (possible safety filter)",
                           request_id, chunk_label)
                raise Exception(f"No candidates returned for {chunk_label} -- content may have been blocked by safety filter")

            candidate = response.candidates[0]
            finish = candidate.finish_reason.name if candidate.finish_reason else "UNKNOWN"

            # Check for blocked content
            if finish in ("SAFETY", "BLOCKED", "PROHIBITED_CONTENT"):
                logger.error("[%s] Gemini BLOCKED %s, finish_reason=%s",
                           request_id, chunk_label, finish)
                raise Exception(f"Content blocked by Gemini safety filter ({finish}) for {chunk_label}")

            # RECITATION is transient -- retry the whole chunk before falling back
            if finish == "RECITATION":
                logger.warning("[%s] Gemini RECITATION %s (attempt %d/%d), retrying chunk...",
                             request_id, chunk_label, attempt, max_retries)
                if on_event:
                    await on_event(sse_event("stage", stage="retry",
                        message=f"Content flagged, retrying chunk (attempt {attempt}/{max_retries})..."))
                if attempt == max_retries:
                    # All retries exhausted -- return the empty response so per-page fallback kicks in
                    logger.warning("[%s] RECITATION persisted after %d attempts for %s, falling back to per-page retry",
                                 request_id, max_retries, chunk_label)
                    return response
                await asyncio.sleep(3 * attempt)
                continue

            text = response.text if response.text else ""
            out_len = len(text)

            logger.info("[%s] Gemini API DONE %s in %.1fs -> %d chars, finish=%s",
                       request_id, chunk_label, duration, out_len, finish)
            return response

        except asyncio.TimeoutError:
            logger.error("[%s] Gemini TIMEOUT %s (attempt %d/%d)",
                        request_id, chunk_label, attempt, max_retries)
            if on_event:
                await on_event(sse_event("stage", stage="retry",
                    message=f"Timed out, retrying (attempt {attempt}/{max_retries})..."))
            if attempt == max_retries:
                raise Exception(f"Timed out after {max_retries} attempts for {chunk_label}")
            await asyncio.sleep(5 * attempt)

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                wait_time = float(match.group(1)) + 1 if match else 5 * attempt
                logger.warning("[%s] Rate limited %s (attempt %d/%d), waiting %.0fs",
                             request_id, chunk_label, attempt, max_retries, wait_time)
                if on_event:
                    await on_event(sse_event("stage", stage="retry",
                        message=f"Rate limited, waiting {wait_time:.0f}s (attempt {attempt}/{max_retries})..."))
                await asyncio.sleep(wait_time)
            elif "503" in error_str or "UNAVAILABLE" in error_str:
                wait_time = 5 * attempt
                logger.warning("[%s] Service unavailable %s (attempt %d/%d), waiting %ds",
                             request_id, chunk_label, attempt, max_retries, wait_time)
                if on_event:
                    await on_event(sse_event("stage", stage="retry",
                        message=f"Service unavailable, waiting {wait_time}s (attempt {attempt}/{max_retries})..."))
                await asyncio.sleep(wait_time)
            else:
                logger.error("[%s] Non-retryable error %s: %s",
                           request_id, chunk_label, error_str)
                raise

    raise Exception(f"Failed after {max_retries} retries for {chunk_label}")

# ---------------------------------------------------------------------------
# Process a chunk: call Gemini, validate pages, retry missing individually
# ---------------------------------------------------------------------------

async def process_chunk(content: bytes, chunk_info: dict, total_pages: int,
                        request_id: str, job: dict, model_id=None, on_event=None) -> str:
    """
    Process a PDF chunk through Gemini. Validates page coverage and retries
    missing pages individually. Returns combined markdown for the chunk.
    """
    start_page = chunk_info["start_page"]
    end_page = chunk_info["end_page"]
    chunk_label = f"pages {start_page}-{end_page}"

    # --- First attempt: full chunk ---
    pdf_chunk = split_pdf_pages(content, chunk_info["start_idx"], chunk_info["end_idx"])
    prompt = build_ocr_prompt(start_page, end_page, total_pages)
    pdf_part = types.Part(inline_data=types.Blob(data=pdf_chunk, mime_type="application/pdf"))
    contents_list = [types.Content(role="user", parts=[pdf_part, types.Part.from_text(text=prompt)])]

    response = await call_gemini_with_retry(
        contents_list, model_id=model_id, request_id=request_id,
        chunk_label=chunk_label, on_event=on_event)

    md_text = response.text or ""
    truncated = False
    if response.candidates and response.candidates[0].finish_reason:
        if response.candidates[0].finish_reason.name == "MAX_TOKENS":
            truncated = True

    # --- Validate page coverage ---
    validation = validate_pages(md_text, start_page, end_page)
    found_pages = validation["found"]
    missing_pages = validation["missing"]

    # Track received pages
    job["pages_received"].extend(found_pages)

    if truncated:
        logger.warning("[%s] %s was TRUNCATED (found pages %s, missing %s)",
                      request_id, chunk_label, found_pages, missing_pages)
        job["pages_truncated"].extend(missing_pages if missing_pages else [end_page])

    if missing_pages:
        logger.warning("[%s] MISSING PAGES after %s: %s (found: %s)",
                      request_id, chunk_label, missing_pages, found_pages)

        # --- Retry missing pages in parallel (max 3 concurrent) ---
        sem = asyncio.Semaphore(3)
        recovered_parts: dict[int, str] = {}  # page_num -> markdown
        pages_done = len(found_pages)

        async def retry_single_page(miss_page: int):
            nonlocal pages_done
            async with sem:
                job["pages_retried"].append(miss_page)
                retry_label = f"page {miss_page} (retry)"
                logger.info("[%s] Retrying individual %s", request_id, retry_label)

                if on_event:
                    await on_event(sse_event("stage", stage="retry",
                        message=f"Page {miss_page} missing, retrying individually...",
                        progress=int((pages_done / total_pages) * 100)))

                try:
                    single_pdf = split_pdf_pages(content, miss_page - 1, miss_page)
                    single_prompt = build_ocr_prompt(miss_page, miss_page, total_pages)
                    single_part = types.Part(inline_data=types.Blob(
                        data=single_pdf, mime_type="application/pdf"))
                    single_contents = [types.Content(role="user", parts=[
                        single_part, types.Part.from_text(text=single_prompt)])]

                    single_response = await call_gemini_with_retry(
                        single_contents, model_id=model_id, request_id=request_id,
                        chunk_label=retry_label, on_event=on_event)

                    single_md = single_response.text or ""
                    if single_md.strip():
                        single_val = validate_pages(single_md, miss_page, miss_page)
                        if miss_page in single_val["found"]:
                            recovered_parts[miss_page] = single_md
                            job["pages_received"].append(miss_page)
                            logger.info("[%s] Page %d recovered successfully", request_id, miss_page)
                        else:
                            recovered_parts[miss_page] = f"<!-- Page {miss_page} -->\n" + single_md
                            job["pages_received"].append(miss_page)
                            logger.warning("[%s] Page %d returned content without marker, injected marker",
                                         request_id, miss_page)
                    else:
                        logger.error("[%s] Page %d retry returned EMPTY", request_id, miss_page)
                        job["pages_empty"].append(miss_page)

                except Exception as e:
                    logger.error("[%s] Page %d retry FAILED: %s", request_id, miss_page, e)

                pages_done += 1
                if on_event:
                    await on_event(sse_event("stage", stage="retry",
                        message=f"Recovered {pages_done}/{total_pages} pages...",
                        progress=int((pages_done / total_pages) * 100)))

        await asyncio.gather(*[retry_single_page(p) for p in missing_pages])

        # Append recovered pages in page order
        for p in sorted(recovered_parts.keys()):
            md_text += "\n\n" + recovered_parts[p]

    elif not md_text.strip():
        # Entire chunk was empty
        logger.error("[%s] %s returned EMPTY response", request_id, chunk_label)
        job["pages_empty"].extend(range(start_page, end_page + 1))

    return md_text

# ---------------------------------------------------------------------------
# Final audit: log page coverage summary
# ---------------------------------------------------------------------------

def audit_job(job: dict, request_id: str):
    """Log a final summary of page coverage."""
    total = job["total_pages"]
    received = sorted(set(job["pages_received"]))
    missing = sorted(set(job["pages_expected"]) - set(received))
    retried = sorted(set(job["pages_retried"]))
    empty = sorted(set(job["pages_empty"]))
    truncated = sorted(set(job["pages_truncated"]))

    coverage_pct = (len(received) / total * 100) if total > 0 else 0

    logger.info("[%s] === PAGE AUDIT ===", request_id)
    logger.info("[%s]   Total pages: %d", request_id, total)
    logger.info("[%s]   Pages received: %d/%d (%.1f%%)", request_id, len(received), total, coverage_pct)

    if missing:
        logger.error("[%s]   MISSING PAGES: %s", request_id, missing)
    else:
        logger.info("[%s]   All pages accounted for", request_id)

    if retried:
        logger.info("[%s]   Pages retried individually: %s", request_id, retried)
    if empty:
        logger.warning("[%s]   Pages with empty response: %s", request_id, empty)
    if truncated:
        logger.warning("[%s]   Pages affected by truncation: %s", request_id, truncated)

    logger.info("[%s] === END AUDIT ===", request_id)

    # Update job with final numbers
    job["pages_received"] = received
    job["pages_missing"] = missing
    job["coverage_pct"] = round(coverage_pct, 1)

# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------

def sse_event(event_type: str, **kwargs) -> str:
    data = {"type": event_type, **kwargs}
    return f"data: {json.dumps(data)}\n\n"

# ---------------------------------------------------------------------------
# POST /convert  (batch mode, plain text response)
# ---------------------------------------------------------------------------

@app.post("/convert", response_class=PlainTextResponse)
async def convert_pdf(file: UploadFile = File(...), model: str = Form(None)):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    model_id = model if model in ALLOWED_MODELS else MODEL_ID
    request_id = uuid.uuid4().hex[:8]
    logger.info("[%s] /convert START file=%s model=%s", request_id, file.filename, model_id)

    try:
        content = await file.read()
        total_pages = get_pdf_page_count(content)
        page_chunks = make_page_chunks(total_pages, PAGES_PER_CHUNK)
        total_chunks = len(page_chunks)

        job = new_job(request_id, file.filename, total_pages, total_chunks)
        global current_job
        current_job = job

        logger.info("[%s] Processing %d chunks (%d pages, %d pages/chunk)",
                   request_id, total_chunks, total_pages, PAGES_PER_CHUNK)
        full_response_text = ""

        for i, chunk_info in enumerate(page_chunks):
            if i > 0:
                await asyncio.sleep(0.2)

            t0 = time.time()

            md_text = await process_chunk(
                content, chunk_info, total_pages, request_id, job, model_id=model_id)

            duration = time.time() - t0
            out_len = len(md_text)
            truncated = any(p in job["pages_truncated"]
                          for p in range(chunk_info["start_page"], chunk_info["end_page"] + 1))

            job["chunks"].append({
                "chunk": i + 1,
                "pages": f"{chunk_info['start_page']}-{chunk_info['end_page']}",
                "duration_sec": round(duration, 1),
                "chars": out_len,
                "truncated": truncated,
                "status": "ok" if md_text.strip() else "empty",
            })

            if md_text.strip():
                full_response_text += md_text + "\n\n"

        # Final audit
        audit_job(job, request_id)

        if not full_response_text.strip():
            raise ValueError("Empty response from Gemini for all chunks")

        current_job = None
        finish_job(job, "success")
        logger.info("[%s] /convert DONE, total output: %d chars", request_id, len(full_response_text))
        return full_response_text.strip()

    except Exception as e:
        logger.error("[%s] /convert FAILED: %s", request_id, e)
        if current_job and current_job.get("request_id") == request_id:
            audit_job(current_job, request_id)
            finish_job(current_job, "failed", str(e))
            current_job = None
        raise HTTPException(status_code=500, detail=str(e) or type(e).__name__)

# ---------------------------------------------------------------------------
# POST /convert-stream  (single file, SSE progress)
# ---------------------------------------------------------------------------

@app.post("/convert-stream")
async def convert_pdf_stream(file: UploadFile = File(...), model: str = Form(None)):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    model_id = model if model in ALLOWED_MODELS else MODEL_ID
    content = await file.read()
    request_id = uuid.uuid4().hex[:8]
    filename = file.filename
    logger.info("[%s] /convert-stream START file=%s model=%s size=%.2fMB",
               request_id, filename, model_id, len(content) / (1024 * 1024))

    async def event_generator():
        global current_job
        job = None
        try:
            yield sse_event("stage", stage="reading", message="Reading PDF...", progress=5)

            total_pages = get_pdf_page_count(content)
            size_mb = len(content) / (1024 * 1024)
            page_chunks = make_page_chunks(total_pages, PAGES_PER_CHUNK)
            total_chunks = len(page_chunks)

            job = new_job(request_id, filename, total_pages, total_chunks)
            current_job = job

            yield sse_event("stage", stage="splitting",
                          message=f"Analyzed: {size_mb:.1f}MB, {total_pages} pages, {total_chunks} chunk(s)",
                          progress=10)
            logger.info("[%s] Split into %d chunks (%d pages)", request_id, total_chunks, total_pages)

            event_queue = asyncio.Queue()

            async def on_retry_event(sse_str):
                await event_queue.put(sse_str)

            full_response_text = ""
            for i, chunk_info in enumerate(page_chunks):
                if i > 0:
                    await asyncio.sleep(0.2)

                chunk_progress = 10 + int((i / total_chunks) * 80)
                pages_label = f"pages {chunk_info['start_page']}-{chunk_info['end_page']}"
                yield sse_event("stage", stage="processing",
                              message=f"Processing chunk {i+1}/{total_chunks} ({pages_label})...",
                              chunk=i+1, totalChunks=total_chunks, progress=chunk_progress)

                t0 = time.time()

                # Run chunk processing as a task so we can send heartbeats
                ocr_task = asyncio.create_task(
                    process_chunk(content, chunk_info, total_pages,
                                 request_id, job, model_id=model_id,
                                 on_event=on_retry_event))

                # Send heartbeats every 30s while the API call is running
                while not ocr_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(ocr_task), timeout=30.0)
                    except asyncio.TimeoutError:
                        elapsed = int(time.time() - t0)
                        yield sse_event("stage", stage="processing",
                                      message=f"Processing chunk {i+1}/{total_chunks} ({pages_label})... {elapsed}s",
                                      chunk=i+1, totalChunks=total_chunks, progress=chunk_progress)
                        # Drain any retry events accumulated
                        while not event_queue.empty():
                            yield event_queue.get_nowait()

                md_text = ocr_task.result()

                duration = time.time() - t0

                # Drain any remaining retry events
                while not event_queue.empty():
                    yield event_queue.get_nowait()

                # Report page validation results
                validation = validate_pages(md_text,
                    chunk_info["start_page"], chunk_info["end_page"])
                pages_found = len(validation["found"])
                pages_expected = len(validation["expected"])
                pages_missing = validation["missing"]

                truncated = any(p in job["pages_truncated"]
                              for p in range(chunk_info["start_page"], chunk_info["end_page"] + 1))

                if truncated:
                    yield sse_event("stage", stage="warning",
                                  message=f"Warning: chunk {i+1} ({pages_label}) was truncated",
                                  progress=chunk_progress)

                if pages_missing and all(p not in job["pages_received"] for p in pages_missing):
                    yield sse_event("stage", stage="warning",
                                  message=f"Warning: pages {pages_missing} still missing after retry",
                                  progress=chunk_progress)

                out_len = len(md_text)
                job["chunks"].append({
                    "chunk": i + 1,
                    "pages": f"{chunk_info['start_page']}-{chunk_info['end_page']}",
                    "duration_sec": round(duration, 1),
                    "chars": out_len,
                    "truncated": truncated,
                    "pages_found": pages_found,
                    "pages_expected": pages_expected,
                    "status": "ok" if md_text.strip() else "empty",
                })

                if md_text.strip():
                    full_response_text += md_text + "\n\n"

                done_progress = 10 + int(((i + 1) / total_chunks) * 80)
                yield sse_event("stage", stage="chunk_done",
                              message=f"Chunk {i+1}/{total_chunks} done ({pages_label}) - {duration:.0f}s, {pages_found}/{pages_expected} pages, {out_len} chars",
                              chunk=i+1, totalChunks=total_chunks, progress=done_progress)

            # Final audit
            audit_job(job, request_id)

            if not full_response_text.strip():
                logger.error("[%s] Empty response from all chunks", request_id)
                yield sse_event("error", message="Empty response from Gemini")
                if job:
                    finish_job(job, "failed", "Empty response")
                    current_job = None
                return

            # Send audit summary before result
            received = len(set(job["pages_received"]))
            total_p = job["total_pages"]
            missing = job.get("pages_missing", [])
            coverage = job.get("coverage_pct", 0)

            if missing:
                yield sse_event("stage", stage="warning",
                              message=f"Audit: {received}/{total_p} pages ({coverage}%). Missing: {missing}",
                              progress=95)
            else:
                yield sse_event("stage", stage="audit",
                              message=f"Audit: {received}/{total_p} pages ({coverage}%) -- all pages captured",
                              progress=95)

            finish_job(job, "success")
            current_job = None
            logger.info("[%s] /convert-stream DONE, total output: %d chars",
                       request_id, len(full_response_text))
            yield sse_event("result", markdown=full_response_text.strip(), progress=100,
                          audit={"pages_received": received, "total_pages": total_p,
                                 "pages_missing": missing, "coverage_pct": coverage})

        except Exception as e:
            logger.error("[%s] /convert-stream FAILED: %s", request_id, e, exc_info=True)
            if job:
                audit_job(job, request_id)
                finish_job(job, "failed", str(e))
                current_job = None
            yield sse_event("error", message=str(e) or type(e).__name__)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    )

# ---------------------------------------------------------------------------
# GET /status  -- current job + recent history
# ---------------------------------------------------------------------------

@app.get("/status")
async def get_status():
    def clean_job(j):
        if j is None:
            return None
        return {k: v for k, v in j.items() if k != "data"}

    history = [clean_job(j) for j in request_history]
    return JSONResponse({
        "current": clean_job(current_job),
        "history": history[:20],
        "model": MODEL_ID,
        "available_models": sorted(ALLOWED_MODELS),
    })

# ---------------------------------------------------------------------------
# GET /logs  -- last N lines of the log file
# ---------------------------------------------------------------------------

@app.get("/logs")
async def get_logs(lines: int = Query(default=100, ge=1, le=5000)):
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return JSONResponse({"lines": all_lines[-lines:]})
    except FileNotFoundError:
        return JSONResponse({"lines": []})

# ---------------------------------------------------------------------------
# Mount Static Files (Frontend) -- must be last
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
