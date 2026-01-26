import google.auth
from googleapiclient.discovery import build

PROJECT_ID = "antigravity-ocr-demo-2026"
OUTPUT_FOLDER_ID = "1z-MeueGZI27tz5M-b3dM50CSRpH1Fgxv"

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def main():
    print("--- Verifying Output Folder ---")
    creds, _ = google.auth.default(scopes=SCOPES)
    service = build('drive', 'v3', credentials=creds)

    results = service.files().list(
        q=f"'{OUTPUT_FOLDER_ID}' in parents and trashed = false",
        fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])

    if not files:
        print("No files found in output folder.")
    else:
        for f in files:
            print(f"Found: {f['name']} ({f['mimeType']})")

if __name__ == "__main__":
    main()
