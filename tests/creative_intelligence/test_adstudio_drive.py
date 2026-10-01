"""Export AI ads to Google Drive for agencies — naming, flags, gating, OAuth.

No live API: every Google call is faked; DB and images live in tmp_path.
"""
from __future__ import annotations

import base64
import json

import pytest

from creative_intelligence.adstudio import drive
from creative_intelligence.db import get_connection, init_db


# ── naming ───────────────────────────────────────────────────────────────────
def test_export_folder_name_sorts_by_date_and_dedupes_same_day():
    assert drive.export_folder_name("Kiwi berry", "2026-10-01", 9, set()) == "2026-10-01 · Kiwi berry · 9 AI ads"
    taken = {"2026-10-01 · Kiwi berry · 1 AI ad", "2026-10-01 · Kiwi berry · 1 AI ad (2)"}
    assert drive.export_folder_name("Kiwi berry", "2026-10-01", 1, taken) == "2026-10-01 · Kiwi berry · 1 AI ad (3)"


def test_names_are_safe_to_download():
    assert drive.clean('Large Cherimoya (Custard/Apple): "XL"') == "Large Cherimoya (Custard-Apple)- -XL-"
    ad = {"kind": "scene", "variant": "pov", "title": "Picnic POV!"}
    assert drive.ad_file_name("Kiwi berry", 3, ad) == "kiwi-berry_03_scene-picnic-pov.png"
    assert drive.ad_file_name("Kiwi berry", 12, {"kind": "variant", "variant": "direct_response"}) == \
        "kiwi-berry_12_direct-response.png"


def test_id_token_email():
    payload = base64.urlsafe_b64encode(json.dumps({"email": "Rob@GoodHillFarms.com"}).encode()).decode().rstrip("=")
    assert drive._id_token_email(f"h.{payload}.s") == "rob@goodhillfarms.com"
    assert drive._id_token_email("garbage") == ""


def test_upload_converts_csv_to_sheet(monkeypatch):
    sent = {}

    class R:
        status_code = 200
        def json(self): return {"id": "f1", "webViewLink": "https://x"}

    def fake_post(url, **kw):
        sent.update(url=url, **kw)
        return R()
    monkeypatch.setattr(drive.requests, "post", fake_post)
    out = drive.upload("tok", "Ad copy", "folder1", b"a,b\n", "text/csv", as_mime=drive.SHEET)
    assert out["id"] == "f1" and sent["params"]["uploadType"] == "multipart"
    assert b'"mimeType": "application/vnd.google-apps.spreadsheet"' in sent["data"]
    assert b'"parents": ["folder1"]' in sent["data"] and sent["headers"]["Authorization"] == "Bearer tok"


# ── routes ───────────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    init_db(path)
    return lambda: get_connection(path)


class FakeDrive:
    """In-memory Drive: folders and files by id."""
    def __init__(self):
        self.items, self.n = {"PARENT": {"name": "Meta Ads", "parent": None}}, 0

    def _new(self, name, parent, **kw):
        self.n += 1
        fid = f"id{self.n}"
        self.items[fid] = {"name": name, "parent": parent, **kw}
        return fid

    def install(self, monkeypatch):
        monkeypatch.setattr(drive, "access_token", lambda rt: "tok")
        monkeypatch.setattr(drive, "get_folder", lambda t, fid: self.items.get(fid))
        monkeypatch.setattr(drive, "child_names", lambda t, p: {v["name"] for v in self.items.values() if v["parent"] == p})
        monkeypatch.setattr(drive, "find_folder", lambda t, name, p: next(
            (k for k, v in self.items.items() if v["parent"] == p and v["name"] == name), None))
        monkeypatch.setattr(drive, "create_folder", lambda t, name, p: self._new(name, p))
        monkeypatch.setattr(drive, "upload_file", lambda t, path, name, p: {
            "id": self._new(name, p), "webViewLink": f"https://drive/{name}"})

        def up(t, name, p, data, mime, as_mime=None):
            fid = self._new(name, p, data=data.decode(), mime=as_mime)
            return {"id": fid, "webViewLink": f"https://sheet/{fid}"}
        monkeypatch.setattr(drive, "upload", up)
        return self

    def children(self, parent):
        return {v["name"]: k for k, v in self.items.items() if v["parent"] == parent}


@pytest.fixture
def client(db, monkeypatch, tmp_path):
    import creative_intelligence.webapp.app as app_module
    monkeypatch.setattr(app_module, "_db", db)
    monkeypatch.setenv("ADSTUDIO_DRIVE_FOLDER_ID", "PARENT")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ADSTUDIO_DRIVE_ACCOUNT", "rob@goodhillfarms.com")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        c.tmp = tmp_path
        yield c


def _seed(client, db, n=3, checked=True):
    pid = client.post("/api/adstudio/products", json={"name": "Kiwi berry", "product_type": "fruit"}).get_json()["id"]
    conn = db()
    ids = []
    with conn:
        b = conn.execute("INSERT INTO adstudio_briefs (product_id, brief_json) VALUES (?, ?)",
                         (pid, json.dumps({"angle": "No fuzz"}))).lastrowid
        for i in range(n):
            img = client.tmp / f"a{i}.png"
            img.write_bytes(b"png")
            ids.append(conn.execute(
                "INSERT INTO adstudio_textads (product_id, brief_id, kind, variant, title, headline, body,"
                " status, image_path, text_checked, review_notes) VALUES (?,?,'variant','bold_type','Bold Type',"
                " ?, 'primary', 'done', ?, ?, 'internal: kerning off')",
                (pid, b, f"Head {i}", str(img), int(checked))).lastrowid)
    return pid, ids


def _connect(db, email="rob@goodhillfarms.com"):
    conn = db()
    with conn:
        conn.execute("INSERT INTO adstudio_drive_auth (id, email, refresh_token) VALUES (1, ?, 'rt')", (email,))


def _export(client, pid, ids, date="2026-10-01"):
    x = client.post("/api/adstudio/drive-export", json={"product_id": pid, "ids": ids, "date": date})
    assert x.status_code == 200, x.get_json()
    x = x.get_json()
    for i in x["ids"]:
        assert client.post(f"/api/adstudio/drive-export/{x['export_id']}/upload/{i}").status_code == 200
    return x, client.post(f"/api/adstudio/drive-export/{x['export_id']}/finish").get_json()


def test_status_and_not_connected(client, db):
    st = client.get("/api/drive/status").get_json()
    assert st["configured"] and not st["connected"] and st["redirect_uri"].endswith("/drive/callback")
    pid, ids = _seed(client, db)
    r = client.post("/api/adstudio/drive-export", json={"product_id": pid, "ids": ids})
    assert r.status_code == 502 and "Connect Google Drive" in r.get_json()["error"]


def test_export_folders_files_sheet_and_flags(client, db, monkeypatch):
    fake = FakeDrive().install(monkeypatch)
    _connect(db)
    pid, ids = _seed(client, db)
    x, fin = _export(client, pid, ids[:2])

    # Meta Ads / Kiwi berry / 2026-10-01 · Kiwi berry · 2 AI ads
    prod_folder = fake.children("PARENT")["Kiwi berry"]
    assert x["folder_name"] == "2026-10-01 · Kiwi berry · 2 AI ads"
    files = fake.children(fake.children(prod_folder)[x["folder_name"]])
    assert set(files) == {"kiwi-berry_01_bold-type.png", "kiwi-berry_02_bold-type.png",
                          "Ad copy — 2026-10-01 · Kiwi berry · 2 AI ads"}
    sheet = fake.items[files["Ad copy — 2026-10-01 · Kiwi berry · 2 AI ads"]]
    assert sheet["mime"] == drive.SHEET and "Head 0" in sheet["data"] and "No fuzz" in sheet["data"]
    assert "kerning" not in sheet["data"]                       # internal notes stay internal
    assert fin["uploaded"] == 2 and fin["sheet_url"]

    # flags + "not exported" filter
    d = client.get(f"/api/adstudio/textads?done=1&product_id={pid}").get_json()
    flagged = {a["id"]: a["exports"] for a in d["ads"]}
    assert flagged[ids[0]][0]["folder_name"] == x["folder_name"] and flagged[ids[2]] == []
    assert d["counts"]["notexported"] == 1
    left = client.get(f"/api/adstudio/textads?done=1&review=notexported&product_id={pid}").get_json()["ads"]
    assert [a["id"] for a in left] == [ids[2]]

    # same day again → new folder "(2)", product folder reused, ad flagged twice
    x2, _ = _export(client, pid, [ids[0]])
    assert x2["folder_name"] == "2026-10-01 · Kiwi berry · 1 AI ad"
    x3, _ = _export(client, pid, [ids[0]])
    assert x3["folder_name"] == "2026-10-01 · Kiwi berry · 1 AI ad (2)"
    assert list(fake.children("PARENT")) == ["Kiwi berry"]
    a0 = next(a for a in client.get(f"/api/adstudio/textads?done=1&product_id={pid}").get_json()["ads"]
              if a["id"] == ids[0])
    assert len(a0["exports"]) == 3 and a0["exports"][0]["folder_name"] == x3["folder_name"]


def test_upload_retry_does_not_duplicate(client, db, monkeypatch):
    fake = FakeDrive().install(monkeypatch)
    _connect(db)
    pid, ids = _seed(client, db, n=1)
    x = client.post("/api/adstudio/drive-export", json={"product_id": pid, "ids": ids}).get_json()
    for _ in range(2):
        client.post(f"/api/adstudio/drive-export/{x['export_id']}/upload/{ids[0]}")
    assert sum(1 for v in fake.items.values() if v["name"].endswith(".png")) == 1
    assert client.post(f"/api/adstudio/drive-export/{x['export_id']}/upload/999").status_code == 404


def test_only_checked_unrejected_rendered_ads_can_go_out(client, db, monkeypatch):
    FakeDrive().install(monkeypatch)
    _connect(db)
    pid, ids = _seed(client, db, n=2, checked=False)
    conn = db()
    with conn:
        conn.execute("UPDATE adstudio_textads SET text_checked=1, review_status='rejected' WHERE id=?", (ids[1],))
    r = client.post("/api/adstudio/drive-export", json={"product_id": pid, "ids": ids})
    assert r.status_code == 400
    assert r.get_json()["problems"] == {"text not checked": [ids[0]], "rejected": [ids[1]]}


def test_callback_checks_state_and_account(client, db, monkeypatch):
    import creative_intelligence.webapp.app as app_module
    r = client.get("/drive/connect?product_id=4")
    assert r.status_code == 302 and "accounts.google.com" in r.location
    state = next(iter(app_module._DRIVE_STATES))
    assert client.get("/drive/callback?state=forged&code=x").status_code == 400

    monkeypatch.setattr(drive, "exchange_code", lambda code, uri: ("rt", "someone@gmail.com"))
    assert client.get(f"/drive/callback?state={state}&code=x").status_code == 403
    assert not client.get("/api/drive/status").get_json()["connected"]

    client.get("/drive/connect?product_id=4")
    state = next(iter(app_module._DRIVE_STATES))
    monkeypatch.setattr(drive, "exchange_code", lambda code, uri: ("rt", "rob@goodhillfarms.com"))
    r = client.get(f"/drive/callback?state={state}&code=x")
    assert r.status_code == 302 and r.location.endswith("/adstudio?product_id=4&drive=connected")
    assert client.get("/api/drive/status").get_json()["email"] == "rob@goodhillfarms.com"
