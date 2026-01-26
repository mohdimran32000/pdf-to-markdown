# Gemini 3 PDF OCR Web App

A modern, local web application that uses **Google Gemini 3 Flash Preview** to convert PDF documents into perfect Markdown.

## Features
*   **Drag & Drop UI**: Simple, responsive interface.
*   **Instant Conversion**: Uses Gemini 3's native multimodal capabilities.
*   **Split View**: Compare original PDF side-by-side with the generated Markdown.
*   **Secure**: Files are processed in-memory and sent directly to Vertex AI (files are not stored permanently unless configured).

## Prerequisites
1.  **Python 3.8+**
2.  **Google Cloud Project** with:
    *   Vertex AI API enabled.
    *   **Gemini 3 Flash Preview** enabled (via Model Garden).
3.  **Authentication**:
    *   `gcloud auth application-default login`

## Installation
1.  Clone the repository.
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

## How to Run

### Web Application (Recommended)
Double-click `run_app.bat` or run:
```powershell
python -m uvicorn app.main:app --reload
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

### Background Script
To process files from Google Drive:
```powershell
python vertex_ocr_drive.py
```

## detailed Troubleshooting
If you see "404 Model Not Found":
*   Go to Google Cloud Console -> Vertex AI -> Model Garden.
*   Search for "Gemini 3 Flash Preview".
*   Click **Enable**.
