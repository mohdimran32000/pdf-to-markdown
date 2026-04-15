import os
import io
import time
import google.auth
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google import genai
from google.genai import types

# --- Configuration ---
PROJECT_ID = "antigravity-ocr-demo-2026"
LOCATION = "global" # Verified working for Gemini 3 Preview
MODEL_ID = "gemini-3.1-pro-preview" 

# Folders from your n8n workflow
INPUT_FOLDER_ID = "1juGE8k65V7bVKxv7cx-OU3z6sJEg7HEk"      # 01_Input/
OUTPUT_FOLDER_ID = "1z-MeueGZI27tz5M-b3dM50CSRpH1Fgxv"     # 02_Output_Markdown/
COMPLETED_FOLDER_ID = "1jv4QHka_H8xY28V1RNHa5bEOuBReoTDP"  # 03_Completed/

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/cloud-platform']

class GoogleDriveManager:
    def __init__(self, credentials):
        self.service = build('drive', 'v3', credentials=credentials)

    def list_pdfs(self, folder_id):
        """Lists PDF files in the specified folder."""
        # Using a broader query to ensure we find files
        query = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
        results = self.service.files().list(
            q=query, fields="files(id, name)").execute()
        return results.get('files', [])

    def download_file(self, file_id):
        """Downloads a file's content as bytes."""
        request = self.service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        return fh.getvalue()

    def upload_file(self, folder_id, file_path, mime_type='application/pdf'):
        """Uploads a local file to Drive."""
        file_name = os.path.basename(file_path)
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        media = MediaIoBaseUpload(open(file_path, 'rb'),
                                  mimetype=mime_type,
                                  resumable=True)
        file = self.service.files().create(body=file_metadata,
                                           media_body=media,
                                           fields='id').execute()
        return file.get('id')

    def upload_markdown(self, folder_id, file_name, content):
        """Uploads markdown content to Drive."""
        file_metadata = {
            'name': file_name,
            'parents': [folder_id],
            'mimeType': 'text/markdown'
        }
        media = MediaIoBaseUpload(io.BytesIO(content.encode('utf-8')),
                                  mimetype='text/markdown',
                                  resumable=True)
        file = self.service.files().create(body=file_metadata,
                                           media_body=media,
                                           fields='id').execute()
        return file.get('id')

    def move_file(self, file_id, old_parent, new_parent):
        """Moves a file from one folder to another."""
        # Retrieve the existing parents to remove
        file = self.service.files().get(fileId=file_id,
                                        fields='parents').execute()
        previous_parents = ",".join(file.get('parents'))
        
        # Move the file by adding the new parent and removing the old one
        self.service.files().update(fileId=file_id,
                                    addParents=new_parent,
                                    removeParents=previous_parents,
                                    fields='id, parents').execute()

class GeminiProcessor:
    def __init__(self, project_id, location, model_id):
        # Initialize Google GenAI Client (Handles Vertex AI Preview)
        self.client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location
        )
        self.model_id = model_id

    def process_document(self, pdf_bytes, mime_type="application/pdf"):
        """Sends PDF content to Gemini for OCR conversion."""
        prompt = """
<system_instruction>
You are an expert, highly precise Document OCR Engine. Your objective is to extract text and structure from this document.
</system_instruction>

<formatting_rules>
1. Text Content: Preserve ALL text exactly as written. Use standard markdown for headings (#, ##, ###) and lists. No summaries, no omissions.
2. Equations: Use strict LaTeX ($...$) for mathematical formulas. You MUST use proper macros (e.g., `\tan`, `\cos^{-1}`) instead of plain text for math.
3. Images/Diagrams: Output [Image: brief summary of what is shown].
4. TABLES (CRITICAL): 
   - You MUST output ALL tables using strictly well-formed HTML tags (<table>, <tr>, <th>, <td>). 
   - You MUST use `colspan` and `rowspan` to accurately recreate merged cells.
   - You are STRICTLY FORBIDDEN from generating piped markdown tables (e.g., `| Header | Header |`).
   - If you detect tabular data, immediately open a <table> tag.
</formatting_rules>

<output_format>
Output ONLY the requested content following the rules above. Do not include any conversational preamble.
</output_format>
"""
        
        # Construct content using types.Part
        try:
             content_part = types.Part.from_bytes(data=pdf_bytes, mime_type=mime_type)
        except AttributeError:
             # Fallback if from_bytes is not available in installed version
             content_part = types.Part(
                 inline_data=types.Blob(
                     data=pdf_bytes, 
                     mime_type=mime_type
                 )
             )

        response = self.client.models.generate_content(
            model=self.model_id,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        content_part,
                        types.Part.from_text(text=prompt)
                    ]
                )
            ]
        )

        if response.text:
            return response.text
        else:
            raise Exception("No content generated from Gemini.")

def main():
    print("--- Starting Vertex AI PDF OCR (Gemini 3) ---")
    
    # 1. Authenticate for Drive (Classic SCOPES)
    # We use list to ensure we get a refreshable credential object with correct scopes
    # The GenAI client will use ADC or pick up the env, but we can relies on the fact that
    # google.auth.default() with scopes likely provides the right environment.
    credentials, project = google.auth.default(scopes=SCOPES)
    if not project:
         credentials, project = google.auth.default(scopes=SCOPES, quota_project_id=PROJECT_ID)

    drive_manager = GoogleDriveManager(credentials)
    
    # Initialize Processor (Uses implicit ADC or environment, which matches our auth)
    gemini_processor = GeminiProcessor(PROJECT_ID, LOCATION, MODEL_ID)

    # 2. List Files
    print(f"Checking Input Folder ({INPUT_FOLDER_ID})...")
    files = drive_manager.list_pdfs(INPUT_FOLDER_ID)
    
    if not files:
        print("No PDF files found in input folder.")
        # Optional: Checking for test file locally to re-upload if empty, 
        # but simpler to just let the user know.
        return

    print(f"Found {len(files)} files to process.")

    # 3. Process Loop
    for file in files:
        file_id = file['id']
        file_name = file['name']
        print(f"\nProcessing: {file_name} ({file_id})")

        try:
            # A. Download
            pdf_bytes = drive_manager.download_file(file_id)
            print("  - Downloaded.")

            # B. OCR with Vertex AI
            print(f"  - Sending to Vertex AI ({MODEL_ID})...")
            markdown_text = gemini_processor.process_document(pdf_bytes)
            print("  - OCR Complete.")

            # C. Upload Markdown
            new_file_name = os.path.splitext(file_name)[0] + ".md"
            drive_manager.upload_markdown(OUTPUT_FOLDER_ID, new_file_name, markdown_text)
            print(f"  - Uploaded markdown: {new_file_name}")

            # D. Move Original
            drive_manager.move_file(file_id, INPUT_FOLDER_ID, COMPLETED_FOLDER_ID)
            print("  - Moved original to Completed folder.")

        except Exception as e:
            print(f"  ! ERROR processing {file_name}: {e}")
            # Write error to file for debugging
            with open("last_error.txt", "w") as f:
                f.write(str(e))

    print("\n--- Batch Complete ---")

if __name__ == "__main__":
    main()
