"""Shared fixtures for API level tests.

The app keeps one ``config`` singleton and one SQLite connection per thread,
so isolation has two rules:

  * redirect the data directory once per test module, before the app starts;
  * clear every catalog table (including the FTS mirror) before each test,
    otherwise reused rowids make old keyword hits leak into new tests.
"""
from __future__ import annotations

import os
import tempfile

# These must be set before keepstack.config is imported. conftest is imported
# ahead of every test module, which makes this the reliable hook.
os.environ.setdefault("KEEPSTACK_AI_ENABLED", "false")
os.environ.setdefault("KEEPSTACK_OCR_ENABLED", "false")
os.environ.setdefault("KEEPSTACK_DATA_DIR", tempfile.mkdtemp(prefix="keepstack-api-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from keepstack import db, ingest  # noqa: E402
from keepstack.app import app  # noqa: E402
from keepstack.audit import now_iso  # noqa: E402
from keepstack.auth import hash_password, make_token  # noqa: E402
from keepstack.config import config  # noqa: E402
from keepstack.db import get_conn, init_db  # noqa: E402

# asset_fts first: it has no foreign key and must be emptied explicitly.
TABLES = ["asset_fts", "asset_tags", "tags", "collection_assets", "collections",
          "assets", "asset_metadata", "audit_log", "saved_searches"]

DEFAULT_STAMP = "2026-01-01T10:00:00.000000+00:00"


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    root = tmp_path_factory.mktemp("keepstack-api")
    config.data_dir = root
    config.db_path = root / "keepstack.db"
    config.blob_dir = root / "blobs"
    config.thumb_dir = root / "thumbnails"
    config.cache_dir = root / "cache"
    config.ensure_dirs()
    db.close_conn()
    init_db()

    conn = get_conn()
    conn.execute(
        "INSERT INTO users (username, password_hash, role, is_active, created_at) "
        "VALUES ('admin', ?, 'admin', 1, ?)",
        (hash_password("admin"), now_iso()),
    )
    conn.commit()
    yield {"root": root}
    db.close_conn()


@pytest.fixture()
def client(catalog):
    conn = get_conn()
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    with TestClient(app) as c:
        c.headers.update({"Authorization": "Bearer " + make_token(1, "admin")})
        yield c


@pytest.fixture()
def anon_client(catalog):
    """No Authorization header: used for the 401 checks."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def make_collection(client):
    def _make(name="Test collection"):
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO collections (uuid, name, kind, created_at) VALUES (?,?, 'folder', ?)",
            (name.lower().replace(" ", "-"), name, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    return _make


@pytest.fixture()
def make_asset(client):
    """Insert a catalog row directly, then rebuild its FTS row.

    Skipping ingest keeps tests fast and deterministic: no images, no AI, and
    full control over created_at and tag sets.
    """
    def _make(*, uuid, title="", description="", created_at=DEFAULT_STAMP,
              media_type="image", status="active", tags=(), collection_id=None):
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO assets (uuid, sha256, filename, original_filename, mime_type, "
            "media_type, ext, size, title, description, status, storage_key, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, uuid, uuid + ".jpg", uuid + ".jpg", "image/jpeg", media_type, "jpg",
             1024, title, description, status, uuid + ".bin", created_at, created_at),
        )
        asset_id = cur.lastrowid
        for name in tags:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
            conn.execute("INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?,?)",
                         (asset_id, tag_id))
        if collection_id:
            conn.execute(
                "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id) VALUES (?,?)",
                (collection_id, asset_id))
        conn.commit()
        ingest.reindex_fts(asset_id)
        return asset_id
    return _make
