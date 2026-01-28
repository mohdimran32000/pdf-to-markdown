from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import google.auth
from google import genai
from google.genai import types
import io
import pypdf

app = FastAPI(title="Gemini 3 PDF OCR")

# CORS (Allow local development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
PROJECT_ID = "antigravity-ocr-demo-2026"
LOCATION = "global"
MODEL_ID = "gemini-3-flash-preview"

# Initialize GenAI Client
try:
    # Use ADC
    creds, project = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION
    )
    print(f"GenAI Client initialized for {MODEL_ID}")
except Exception as e:
    print(f"Failed to initialize GenAI Client: {e}")
    client = None

def smart_split_pdf(content: bytes) -> list[bytes]:
    """
    Splits PDF based on logic:
    1. If size > 5MB AND pages < 80: Split into ~5MB chunks.
    2. If pages > 80 AND size < 5MB: Split into 80-page chunks.
    3. Else: Return as single chunk.
    """
    
    TOTAL_SIZE_MB = len(content) / (1024 * 1024)
    MAX_CHUNK_SIZE_BYTES = 5 * 1024 * 1024
    
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        total_pages = len(reader.pages)
        chunks = []

        # Logic 1: High Density (Size > 5MB, Pages < 80) -> Split by Size
        if TOTAL_SIZE_MB > 5 and total_pages < 80:
            print(f"Splitting by SIZE: {TOTAL_SIZE_MB:.2f}MB, {total_pages} pages")
            
            # FINAL IMPLEMENTATION FOR SIZE SPLIT
            chunks = []
            current_batch_pages = []
            
            for page in reader.pages:
                # Try adding to current batch
                test_writer = pypdf.PdfWriter()
                for p in current_batch_pages:
                    test_writer.add_page(p)
                test_writer.add_page(page)
                
                test_stream = io.BytesIO()
                test_writer.write(test_stream)
                
                if test_stream.tell() > MAX_CHUNK_SIZE_BYTES:
                    if not current_batch_pages:
                        # Single page is huge, must accept it
                        single_writer = pypdf.PdfWriter()
                        single_writer.add_page(page)
                        out = io.BytesIO()
                        single_writer.write(out)
                        chunks.append(out.getvalue())
                    else:
                        # Commit previous batch
                        final_writer = pypdf.PdfWriter()
                        for p in current_batch_pages:
                            final_writer.add_page(p)
                        out = io.BytesIO()
                        final_writer.write(out)
                        chunks.append(out.getvalue())
                        
                        # Start new batch with current page
                        current_batch_pages = [page]
                else:
                    current_batch_pages.append(page)
            
            # Leftovers
            if current_batch_pages:
                w = pypdf.PdfWriter()
                for p in current_batch_pages:
                    w.add_page(p)
                out = io.BytesIO()
                w.write(out)
                chunks.append(out.getvalue())

        # Logic 2: Many Pages (Pages > 80, Size < 5MB) -> Split by Page Count
        elif total_pages > 80 and TOTAL_SIZE_MB < 5:
            print(f"Splitting by PAGE COUNT: {total_pages} pages")
            CHUNK_SIZE = 80
            for i in range(0, total_pages, CHUNK_SIZE):
                writer = pypdf.PdfWriter()
                for page in reader.pages[i : i + CHUNK_SIZE]:
                    writer.add_page(page)
                out = io.BytesIO()
                writer.write(out)
                chunks.append(out.getvalue())

        # Logic 3: No Split
        else:
            return [content]
            
        return chunks

    except Exception as e:
        print(f"Error splitting PDF: {e}")
        # Fallback to original
        return [content]

@app.post("/convert", response_class=PlainTextResponse)
async def convert_pdf(file: UploadFile = File(...)):
    """
    Uploads a PDF, processes it with Gemini 3, and returns Markdown.
    Splits large PDFs automatically.
    """
    if not client:
        raise HTTPException(status_code=500, detail="LLM Client not initialized")
    
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        # Read file content
        content = await file.read()
        
        # Split PDF if necessary
        pdf_chunks = smart_split_pdf(content)
        print(f"Processing {len(pdf_chunks)} chunks...")
        
        full_response_text = ""
        
        for i, chunk_data in enumerate(pdf_chunks):
            # Construct Prompt
            prompt = f"""
            Convert this document (Part {i+1}/{len(pdf_chunks)}) to markdown format.
            CRITICAL REQUIREMENTS:
            1. Preserve ALL text exactly as written.
            2. Convert tables to markdown tables.
            3. Maintain heading hierarchy (#, ##).
            4. Describe images/diagrams in [Image: ...] format.
            5. No preamble, ONLY output the markdown.
            """

            # Construct Request
            pdf_part = types.Part(
                 inline_data=types.Blob(
                     data=chunk_data, 
                     mime_type="application/pdf"
                 )
             )

            response = client.models.generate_content(
                model=MODEL_ID,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            pdf_part,
                            types.Part.from_text(text=prompt)
                        ]
                    )
                ]
            )
            
            if response.text:
                full_response_text += response.text + "\n\n"
        
        if not full_response_text:
            raise ValueError("Empty response from Gemini")

        return full_response_text.strip()

    except Exception as e:
        print(f"Error processing file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Mount Static Files (Frontend)
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
