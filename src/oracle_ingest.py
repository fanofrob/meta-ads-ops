"""
oracle_ingest.py
Ingests Google Drive documents into the GHF Oracle vector database.

Reads all docs/sheets/PDFs from configured Drive folders, chunks text,
embeds with OpenAI text-embedding-3-small, and upserts into Supabase.

Skips files that haven't changed since last ingestion.
"""

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests

socket.setdefaulttimeout(60)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from openai import OpenAI

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DRIVE_SOURCES = [
    {"folder_id": "1wNgt_ZGdwiHfRx_L-QzBZpgN5RMTncUw", "label": "daily_brief"},
    {"folder_id": "1lZ6pYJBXrJi7iq6L9sCJEWiDcVD5kpI0", "label": "snapshots"},
    {"folder_id": "1atrz7hBxvP2pPs22OtttcSRPPO6ZANxH", "label": "reports"},
]

STANDALONE_FILES = [
    {"file_id": "18-WfxcnVOCpQ8rZRWzXlIKQVaom67x3zPbnUmTtakQI", "label": "org_context"},
]

CHUNK_CHARS   = 2000  # ~500 tokens (4 chars per token)
OVERLAP_CHARS = 200   # ~50 tokens
EMBED_MODEL   = "text-embedding-3-small"
EMBED_BATCH   = 100   # chunks per OpenAI request
CREDENTIALS_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "credentials/sheets_service_account.json")

SKIP_MIME_TYPES = {
    "application/vnd.google-apps.folder",
    "application/vnd.google-apps.shortcut",
}

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def load_google_credentials():
    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(json_str)
        tmp.flush()
        path = tmp.name
    else:
        path = CREDENTIALS_PATH

    return service_account.Credentials.from_service_account_file(
        path,
        scopes=[
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
        ]
    )


class SupabaseClient:
    """Minimal Supabase REST client — avoids the hanging websocket in the SDK."""
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

    def _rest(self, path):
        return f"{self.url}/rest/v1/{path}"

    def select(self, table, columns="*", **filters):
        params = {"select": columns}
        for k, v in filters.items():
            params[k] = f"eq.{v}"
        r = requests.get(self._rest(table), headers=self.headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def upsert(self, table, rows, on_conflict):
        headers = {**self.headers, "Prefer": f"resolution=merge-duplicates,return=minimal"}
        r = requests.post(
            self._rest(table),
            headers=headers,
            params={"on_conflict": on_conflict},
            json=rows if isinstance(rows, list) else [rows],
            timeout=30,
        )
        r.raise_for_status()

    def delete(self, table, **filters):
        params = {}
        for k, v in filters.items():
            params[k] = f"eq.{v}"
        r = requests.delete(self._rest(table), headers=self.headers, params=params, timeout=30)
        r.raise_for_status()

    def insert(self, table, rows):
        r = requests.post(
            self._rest(table),
            headers=self.headers,
            json=rows if isinstance(rows, list) else [rows],
            timeout=60,
        )
        if not r.ok:
            print(f"  [ERROR] {r.status_code} inserting into {table}: {r.text[:500]}")
        r.raise_for_status()



# ---------------------------------------------------------------------------
# Drive file listing
# ---------------------------------------------------------------------------

def list_folder_files(drive, folder_id):
    files = []
    page_token = None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,mimeType,modifiedTime)",
            pageToken=page_token,
            pageSize=100
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def get_file_metadata(drive, file_id):
    return drive.files().get(
        fileId=file_id,
        fields="id,name,mimeType,modifiedTime"
    ).execute()


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(drive, sheets, file_meta) -> str:
    mime = file_meta["mimeType"]
    fid  = file_meta["id"]

    if mime == "application/vnd.google-apps.document":
        return _export_as_text(drive, fid, "text/plain")

    if mime == "application/vnd.google-apps.spreadsheet":
        return ""  # spreadsheets are handled separately via extract_sheet_chunks

    if mime == "application/json":
        content = _download_bytes(drive, fid).decode("utf-8", errors="replace")
        try:
            return json.dumps(json.loads(content), indent=2)
        except Exception:
            return content

    if mime == "application/pdf":
        return _extract_pdf(drive, fid)

    if mime.startswith("text/"):
        return _download_bytes(drive, fid).decode("utf-8", errors="replace")

    print(f"  [SKIP] Unsupported MIME type: {mime} ({file_meta['name']})")
    return ""


def _export_as_text(drive, file_id, mime_type) -> str:
    request = drive.files().export_media(fileId=file_id, mimeType=mime_type)
    buf = BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8", errors="replace")


def _download_bytes(drive, file_id) -> bytes:
    request = drive.files().get_media(fileId=file_id)
    buf = BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def extract_sheet_chunks(sheets_svc, spreadsheet_id, file_name) -> list[dict]:
    """
    Returns one chunk dict per data row for every sheet tab.
    Each chunk is self-contained: metric name + all its date:value pairs.
    Works regardless of how many label columns exist or where the header row is.
    """
    SKIP_VALUES = {"#VALUE!", "#N/A", "#DIV/0!", "#REF!", "#NULL!", ""}

    meta = sheets_svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_names = [s["properties"]["title"] for s in meta.get("sheets", [])]
    all_chunks = []

    for name in sheet_names:
        try:
            result = sheets_svc.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=name
            ).execute()
            rows = result.get("values", [])
            if not rows:
                continue

            # Find the header row: the one with the most non-empty cells in the first 10 rows
            header_idx = max(range(min(10, len(rows))), key=lambda i: sum(1 for c in rows[i] if str(c).strip()))
            headers = [str(c).strip() for c in rows[header_idx]]

            # Detect label columns: columns whose header doesn't contain a digit or "/"
            # (these are row-label columns like "Category", "Metric", blank, "Week")
            label_col_count = 0
            for h in headers:
                if any(ch.isdigit() or ch == "/" for ch in h):
                    break
                label_col_count += 1
            if label_col_count == 0:
                label_col_count = 1  # always at least one label column

            date_headers = headers[label_col_count:]
            has_dates = len(date_headers) >= 3

            print(f"    Sheet '{name}': {len(rows)} rows, header at row {header_idx}, "
                  f"{label_col_count} label col(s), {len(date_headers)} date cols")

            for row in rows[header_idx + 1:]:
                if not row:
                    continue

                # Build metric name from all label columns
                labels = [str(row[i]).strip() for i in range(label_col_count) if i < len(row)]
                metric_name = " — ".join(l for l in labels if l)
                if not metric_name:
                    continue

                if has_dates:
                    # Time-series row: one chunk with all date:value pairs
                    pairs = []
                    for i, header in enumerate(date_headers):
                        col_idx = label_col_count + i
                        val = str(row[col_idx]).strip() if col_idx < len(row) else ""
                        if header and val and val not in SKIP_VALUES:
                            pairs.append(f"  {header}: {val}")
                    if pairs:
                        content = f"[{name}] {metric_name}\n" + "\n".join(pairs)
                        all_chunks.append({"content": content, "token_count": len(content) // 4})
                else:
                    # Non-time-series: emit as key-value pairs using column headers
                    pairs = []
                    for i, header in enumerate(headers[label_col_count:]):
                        col_idx = label_col_count + i
                        val = str(row[col_idx]).strip() if col_idx < len(row) else ""
                        if header and val and val not in SKIP_VALUES:
                            pairs.append(f"  {header}: {val}")
                    if pairs:
                        content = f"[{name}] {metric_name}\n" + "\n".join(pairs)
                        all_chunks.append({"content": content, "token_count": len(content) // 4})

        except Exception as e:
            print(f"    [WARN] Sheet '{name}' error: {e}")

    print(f"    → {len(all_chunks)} row-chunks across {len(sheet_names)} sheet(s)")
    return all_chunks


def _extract_pdf(drive, file_id) -> str:
    # Try Google's text export first (works for PDF-as-Google-Doc)
    try:
        return _export_as_text(drive, file_id, "text/plain")
    except Exception:
        pass
    # Fallback: download and parse with pypdf
    try:
        from pypdf import PdfReader
        data = _download_bytes(drive, file_id)
        reader = PdfReader(BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        print(f"  [WARN] PDF extraction failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, file_name: str) -> list[dict]:
    if not text.strip():
        return []

    chunks = []
    start  = 0

    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        chunk = text[start:end]

        # Always prefer to break at a paragraph boundary (\n\n) so that
        # transposed metric blocks (each separated by \n\n) stay intact.
        if end < len(text):
            boundary = chunk.rfind("\n\n")
            if boundary > 0:
                chunk = chunk[:boundary]

        chunk = chunk.strip()
        if chunk:
            chunks.append({
                "content":     chunk,
                "token_count": len(chunk) // 4,  # rough estimate
            })
        start += max(CHUNK_CHARS // 2, len(chunk) - OVERLAP_CHARS) if chunk else CHUNK_CHARS

    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_chunks(openai_client, chunks: list[dict]) -> list[list[float]]:
    texts = [c["content"] for c in chunks]
    embeddings = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i + EMBED_BATCH]
        resp  = openai_client.embeddings.create(model=EMBED_MODEL, input=batch)
        embeddings.extend([item.embedding for item in resp.data])
        if i + EMBED_BATCH < len(texts):
            time.sleep(0.1)  # gentle rate limiting
    return embeddings


# ---------------------------------------------------------------------------
# Supabase upsert
# ---------------------------------------------------------------------------

def get_stored_modified(supabase: SupabaseClient, drive_file_id) -> datetime | None:
    data = supabase.select("ghf_documents", columns="modified_at", drive_file_id=drive_file_id)
    if data:
        return datetime.fromisoformat(data[0]["modified_at"])
    return None


def upsert_document(supabase: SupabaseClient, file_meta, label, chunk_count) -> str:
    """Upserts document record and returns its uuid."""
    headers = {**supabase.headers, "Prefer": "resolution=merge-duplicates,return=representation"}
    r = requests.post(
        supabase._rest("ghf_documents"),
        headers=headers,
        params={"on_conflict": "drive_file_id"},
        json=[{
            "drive_file_id": file_meta["id"],
            "file_name":     file_meta["name"],
            "mime_type":     file_meta["mimeType"],
            "folder_label":  label,
            "modified_at":   file_meta["modifiedTime"],
            "ingested_at":   datetime.now(timezone.utc).isoformat(),
            "chunk_count":   chunk_count,
        }],
        timeout=30,
    )
    if not r.ok:
        print(f"  [ERROR] upsert_document {r.status_code}: {r.text[:300]}")
    r.raise_for_status()
    return r.json()[0]["id"]


def upsert_chunks(supabase: SupabaseClient, document_id, drive_file_id, file_name, label, chunks, embeddings):
    supabase.delete("ghf_chunks", drive_file_id=drive_file_id)

    rows = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        rows.append({
            "document_id":   document_id,
            "drive_file_id": drive_file_id,
            "chunk_index":   i,
            "content":       chunk["content"],
            "token_count":   chunk["token_count"],
            "embedding":     "[" + ",".join(str(x) for x in embedding) + "]",
            "metadata": {
                "file_name":    file_name,
                "folder_label": label,
                "chunk_index":  i,
                "total_chunks": len(chunks),
            }
        })

    for i in range(0, len(rows), 50):
        supabase.insert("ghf_chunks", rows[i:i+50])


# ---------------------------------------------------------------------------
# Process a single file
# ---------------------------------------------------------------------------

def process_file(drive, sheets, openai_client, supabase, file_meta, label, force=False):
    fid   = file_meta["id"]
    fname = file_meta["name"]
    mime  = file_meta["mimeType"]

    if mime in SKIP_MIME_TYPES:
        return

    drive_modified = datetime.fromisoformat(
        file_meta["modifiedTime"].replace("Z", "+00:00")
    )
    stored_modified = get_stored_modified(supabase, fid)

    if not force and stored_modified and stored_modified >= drive_modified:
        print(f"  [SKIP] {fname} — unchanged")
        return

    print(f"  [INGEST] {fname} ({mime})")

    if mime == "application/vnd.google-apps.spreadsheet":
        # Spreadsheets: one chunk per data row (metric + all date:value pairs)
        # This guarantees metric names always travel with their values, regardless of layout.
        chunks = extract_sheet_chunks(sheets, fid, fname)
    else:
        print(f"    Extracting text...")
        text = extract_text(drive, sheets, file_meta)
        print(f"    Extracted {len(text)} chars")
        if not text.strip():
            print(f"    [WARN] No text extracted from {fname}")
            return
        chunks = chunk_text(text, fname)

    if not chunks:
        return

    print(f"    Chunked into {len(chunks)} chunks")
    print(f"    Embedding...")
    embeddings = embed_chunks(openai_client, chunks)
    print(f"    Upserting document...")
    document_id = upsert_document(supabase, file_meta, label, len(chunks))
    print(f"    Upserting chunks...")
    upsert_chunks(supabase, document_id, fid, fname, label, chunks, embeddings)

    print(f"    [OK] {len(chunks)} chunks ingested")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    force = "--force" in sys.argv
    if force:
        print("⚡ Force mode: re-ingesting all files regardless of modifiedTime\n")
    print("GHF Oracle ingestion starting...\n")

    creds = load_google_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"].strip())
    supabase = SupabaseClient(os.environ["SUPABASE_URL"].strip(), os.environ["SUPABASE_SERVICE_KEY"].strip())

    total_files = 0
    total_ingested = 0

    # Process folder sources
    for source in DRIVE_SOURCES:
        label     = source["label"]
        folder_id = source["folder_id"]
        print(f"\n[Folder: {label}]")
        files = list_folder_files(drive, folder_id)
        print(f"  Found {len(files)} files")
        for f in files:
            total_files += 1
            before = total_ingested
            process_file(drive, sheets, openai_client, supabase, f, label, force=force)
            if total_ingested > before:
                total_ingested += 1

    # Process standalone files
    for item in STANDALONE_FILES:
        label   = item["label"]
        file_id = item["file_id"]
        print(f"\n[File: {label}]")
        file_meta = get_file_metadata(drive, file_id)
        total_files += 1
        process_file(drive, sheets, openai_client, supabase, file_meta, label, force=force)

    print(f"\nDone. Processed {total_files} files.")


if __name__ == "__main__":
    main()
