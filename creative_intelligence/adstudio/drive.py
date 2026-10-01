"""
Google Drive exports for Ad Studio — hand finished ads to outside agencies.

Layout in Drive:
    Meta Ads/                                  (ADSTUDIO_DRIVE_FOLDER_ID)
      Kiwi Berry/                              one folder per product, reused
        2026-10-01 · Kiwi Berry · 9 AI ads/    one folder per export
          kiwi-berry_01_direct-response.png
          …
          Ad copy — 2026-10-01 · Kiwi Berry    Google Sheet: copy per file

Auth: the app signs in once as the folder's owner (OAuth, Drive scope) and
keeps the refresh token in the DB. A service account can't be used — the
folder is in a personal My Drive, where service accounts have no storage.

Plain REST over `requests` (no google client libraries needed).
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
# Full Drive scope: the app must write into an existing folder it didn't create.
SCOPES = "openid email https://www.googleapis.com/auth/drive"
FOLDER = "application/vnd.google-apps.folder"
SHEET = "application/vnd.google-apps.spreadsheet"
TIMEOUT = 120


class DriveError(RuntimeError):
    pass


def _cfg() -> tuple[str, str]:
    import os
    cid = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    return cid, secret


def configured() -> bool:
    return all(_cfg())


def folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


# ── OAuth ────────────────────────────────────────────────────────────────────
def auth_url(redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode
    cid, _ = _cfg()
    return AUTH_URL + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state,
    })


def exchange_code(code: str, redirect_uri: str) -> tuple[str, str]:
    """Return (refresh_token, email) for the account that just consented."""
    cid, secret = _cfg()
    r = requests.post(TOKEN_URL, data={
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, timeout=30)
    d = r.json()
    if r.status_code != 200 or not d.get("refresh_token"):
        raise DriveError(d.get("error_description") or d.get("error") or "no refresh token returned")
    return d["refresh_token"], _id_token_email(d.get("id_token", ""))


def _id_token_email(id_token: str) -> str:
    """Email claim from the id_token Google just returned over TLS (not re-verified)."""
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "").lower()
    except Exception:
        return ""


_TOKEN: dict[str, Any] = {}


def access_token(refresh_token: str) -> str:
    hit = _TOKEN.get(refresh_token)
    if hit and hit[1] > time.time() + 60:
        return hit[0]
    cid, secret = _cfg()
    r = requests.post(TOKEN_URL, data={
        "refresh_token": refresh_token, "client_id": cid, "client_secret": secret,
        "grant_type": "refresh_token"}, timeout=30)
    d = r.json()
    if r.status_code != 200:
        raise DriveError("Google Drive sign-in expired — reconnect Google Drive "
                         f"({d.get('error_description') or d.get('error')})")
    _TOKEN[refresh_token] = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
    return d["access_token"]


# ── Drive calls ──────────────────────────────────────────────────────────────
def _check(r: requests.Response) -> dict[str, Any]:
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except ValueError:
            msg = r.text
        raise DriveError(f"Drive {r.status_code}: {msg[:300]}")
    return r.json()


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_folder(token: str, folder_id: str) -> dict[str, Any] | None:
    """The folder if it still exists and isn't trashed, else None."""
    r = requests.get(f"{API}/files/{folder_id}", headers=_h(token), timeout=30,
                     params={"fields": "id,name,trashed,mimeType", "supportsAllDrives": "true"})
    if r.status_code == 404:
        return None
    f = _check(r)
    return None if f.get("trashed") or f.get("mimeType") != FOLDER else f


def child_names(token: str, parent_id: str) -> set[str]:
    r = requests.get(f"{API}/files", headers=_h(token), timeout=30, params={
        "q": f"'{parent_id}' in parents and trashed = false", "fields": "files(name)",
        "pageSize": "1000", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    return {f["name"] for f in _check(r).get("files", [])}


def find_folder(token: str, name: str, parent_id: str) -> str | None:
    q = name.replace("\\", "\\\\").replace("'", "\\'")
    r = requests.get(f"{API}/files", headers=_h(token), timeout=30, params={
        "q": f"name = '{q}' and '{parent_id}' in parents and mimeType = '{FOLDER}' and trashed = false",
        "fields": "files(id)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    files = _check(r).get("files", [])
    return files[0]["id"] if files else None


def create_folder(token: str, name: str, parent_id: str) -> str:
    r = requests.post(f"{API}/files", headers=_h(token), timeout=30,
                      params={"supportsAllDrives": "true", "fields": "id"},
                      json={"name": name, "mimeType": FOLDER, "parents": [parent_id]})
    return _check(r)["id"]


def upload(token: str, name: str, parent_id: str, data: bytes, mime: str,
           as_mime: str | None = None) -> dict[str, Any]:
    """Multipart upload; `as_mime` converts (e.g. CSV → Google Sheet)."""
    meta = {"name": name, "parents": [parent_id]}
    if as_mime:
        meta["mimeType"] = as_mime
    boundary = "adstudio" + str(int(time.time() * 1000))
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode() \
        + data + f"\r\n--{boundary}--".encode()
    r = requests.post(UPLOAD, timeout=TIMEOUT, data=body,
                      headers={**_h(token), "Content-Type": f"multipart/related; boundary={boundary}"},
                      params={"uploadType": "multipart", "supportsAllDrives": "true",
                              "fields": "id,name,webViewLink"})
    return _check(r)


def upload_file(token: str, path: str, name: str, parent_id: str) -> dict[str, Any]:
    return upload(token, name, parent_id, Path(path).read_bytes(), "image/png")


# ── naming ───────────────────────────────────────────────────────────────────
def clean(text: str) -> str:
    """Drive allows anything, but agencies download to Mac/Windows: no / \\ : * ? " < > |."""
    t = re.sub(r'[/\\:*?"<>|]+', "-", text or "").strip()
    return re.sub(r"\s+", " ", t)[:120] or "Untitled"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "x"


def export_folder_name(product_name: str, date: str, n_ads: int, taken: set[str]) -> str:
    """'2026-10-01 · Kiwi Berry · 9 AI ads' — date first so exports sort in order.

    A second export on the same day gets ' (2)', ' (3)' …
    """
    base = f"{date} · {clean(product_name)} · {n_ads} AI ad{'s' if n_ads != 1 else ''}"
    name, i = base, 2
    while name in taken:
        name, i = f"{base} ({i})", i + 1
    return name


def ad_file_name(product_name: str, index: int, ad: dict[str, Any]) -> str:
    """'kiwi-berry_01_direct-response.png' / 'kiwi-berry_10_scene-picnic-pov.png'.

    Numbered in export order so the sheet and the folder line up for the agency.
    """
    what = ad["variant"] if ad["kind"] == "variant" else "scene-" + (ad.get("title") or ad["variant"])
    return f"{slug(product_name)}_{index:02d}_{slug(what)}.png"
