from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import google.auth
from google import genai
from google.genai import types
import io

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

@app.post("/convert", response_class=PlainTextResponse)
async def convert_pdf(file: UploadFile = File(...)):
    """
    Uploads a PDF, processes it with Gemini 3, and returns Markdown.
    """
    if not client:
        raise HTTPException(status_code=500, detail="LLM Client not initialized")
    
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        # Read file content
        content = await file.read()
        
        # Construct Prompt
        prompt = """
Convert this document to markdown format.
CRITICAL REQUIREMENTS:
1. Preserve ALL text exactly as written.
2. Convert tables to markdown tables.
3. Maintain heading hierarchy (#, ##).
4. Describe images/diagrams in [Image: ...] format.
5. No preamble, ONLY output the markdown.
"""

        # Construct Request
        # Note: types.Part.from_bytes might vary by SDK version, 
        # using the direct inline_data blob pattern for safety as tested before.
        pdf_part = types.Part(
             inline_data=types.Blob(
                 data=content, 
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
        
        if not response.text:
            raise ValueError("Empty response from Gemini")

        return response.text

    except Exception as e:
        print(f"Error processing file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Mount Static Files (Frontend)
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
