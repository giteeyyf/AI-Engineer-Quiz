"""Generate a synthetic demo catalog so a fresh install has content.

All sample media is generated on the fly (gradients, shapes, labelled
cards) so there is no bundled binary and nothing proprietary. Run with::

    python -m keepstack seed
"""
from __future__ import annotations

import io
import random
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

from . import ingest
from .auth import hash_password
from .audit import now_iso
from .db import get_conn, init_db, transaction

random.seed(7)  # deterministic demo set

_PALETTES = [
    ((94, 132, 247), (24, 28, 38)), ((70, 192, 138), (18, 30, 26)),
    ((242, 96, 122), (34, 18, 24)), ((220, 160, 60), (32, 26, 14)),
    ((150, 110, 220), (24, 18, 34)), ((60, 180, 200), (14, 28, 30)),
]
_SUBJECTS = [
    ("City skyline at dusk", ["architecture", "city", "skyline"]),
    ("Mountain trail map", ["map", "outdoors", "terrain"]),
    ("Quarterly report cover", ["document", "report", "finance"]),
    ("Public transit poster", ["poster", "transit", "civic"]),
    ("Coastal survey photo", ["coast", "survey", "environment"]),
    ("Heritage building facade", ["heritage", "architecture", "history"]),
    ("Park event banner", ["event", "park", "community"]),
    ("Census infographic", ["infographic", "data", "census"]),
    ("River watershed diagram", ["diagram", "water", "environment"]),
    ("Municipal logo mark", ["logo", "brand", "civic"]),
    ("Road maintenance notice", ["notice", "roads", "public-works"]),
    ("Library reading room", ["library", "interior", "culture"]),
]

# Business tags added to the base set so include/exclude combinations have
# something to bite on even without --demo.
_BASE_EXTRA = {
    "City skyline at dusk": ["approved", "public"],
    "Mountain trail map": ["approved"],
    "Quarterly report cover": ["internal"],
    "Public transit poster": ["campaign", "approved"],
    "Coastal survey photo": ["approved", "campaign"],
    "Heritage building facade": ["approved"],
    "Park event banner": ["campaign"],
    "Census infographic": ["internal"],
    "River watershed diagram": ["approved"],
    "Municipal logo mark": ["approved", "public"],
    "Road maintenance notice": ["campaign"],
    "Library reading room": ["approved"],
}


def _make_image(idx: int, label: str) -> bytes:
    fg, bg = _PALETTES[idx % len(_PALETTES)]
    w, h = random.choice([(1200, 800), (800, 1200), (1000, 1000)])
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)
    # soft geometric composition
    for _ in range(6):
        x0, y0 = random.randint(-100, w), random.randint(-100, h)
        r = random.randint(120, 480)
        shade = tuple(min(255, c + random.randint(-30, 40)) for c in fg)
        draw.ellipse([x0, y0, x0 + r, y0 + r], fill=shade)
    draw.rectangle([0, h - 150, w, h], fill=(0, 0, 0))
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except Exception:
        font = ImageFont.load_default()
    # Centered so the caption survives the square centre-crop used by thumbnails.
    draw.text((w / 2, h - 75), label, font=font, fill=(255, 255, 255), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _stamp(minute: int) -> str:
    """Deterministic UTC timestamp in the same shape as ``now_iso``.

    Microseconds are always written, so lexicographic TEXT order matches time
    order even when two rows fall in the same second.
    """
    base = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=minute)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _set_created_at(conn, asset_id: int, stamp: str) -> None:
    """Pin a demo timestamp. created_at is not indexed by FTS, so no reindex."""
    conn.execute("UPDATE assets SET created_at = ?, updated_at = ? WHERE id = ?",
                 (stamp, stamp, asset_id))


def _add_tags(conn, asset_id: int, names) -> None:
    for name in names:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        tid = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id, source) VALUES (?,?, 'manual')",
            (asset_id, tid))


def _set_description(conn, asset_id: int, text: str) -> None:
    conn.execute("UPDATE assets SET description = ? WHERE id = ?", (text, asset_id))
    ingest.reindex_fts(asset_id)


def run_seed(with_demo: bool = False) -> None:
    init_db()
    conn = get_conn()
    # ensure admin exists (startup hook normally does this, but seed runs standalone)
    if not conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
        conn.execute(
            "INSERT INTO users (username, email, full_name, password_hash, role, is_active, created_at) "
            "VALUES ('admin','admin@example.org','Administrator',?, 'admin', 1, ?)",
            (hash_password("admin"), now_iso()))
        conn.commit()
    admin = dict(conn.execute("SELECT * FROM users WHERE username='admin'").fetchone())

    # demo collaborators
    for uname, role in [("curator", "editor"), ("contributor", "contributor"), ("viewer", "viewer")]:
        if not conn.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone():
            conn.execute(
                "INSERT INTO users (username, password_hash, role, is_active, created_at) VALUES (?,?,?,1,?)",
                (uname, hash_password("demo1234"), role, now_iso()))
    conn.commit()

    created = []
    print("Generating synthetic assets...")
    # Deterministic timestamps: several assets share a minute, so the stable
    # sort tie-breaker is visible right after a plain seed.
    stamps = [_stamp(m) for m in (0, 1, 2, 2, 2, 3, 4, 4, 5, 6, 7, 8)]
    for i, (label, tags) in enumerate(_SUBJECTS):
        for variant in range(2):  # two variants each -> 24 assets
            data = _make_image(i + variant, label)
            asset = ingest.ingest_stream(io.BytesIO(data), f"{label.lower().replace(' ', '_')}_{variant+1}.jpg",
                                         user=admin, run_ai=True, title=label if variant == 0 else f"{label} (alt)")
            _add_tags(conn, asset["id"], [*tags, *_BASE_EXTRA.get(label, ())])
            conn.commit()
            _set_created_at(conn, asset["id"], stamps[i])
            ingest.reindex_fts(asset["id"])
            ingest.reindex_embedding(asset["id"])
            created.append(asset["id"])

    # CSV edge cases: Chinese, comma, double quote and a real newline.
    _set_description(conn, created[0], '市中心天际线，黄昏时拍摄，"灯光"刚刚亮起。')
    _set_description(conn, created[1], '多行说明：\n第一行，带逗号\nthird "quoted" line')

    # collections
    import uuid as uuidlib
    cols = {}
    for name, desc in [("Civic Communications", "Posters, banners, notices for public outreach"),
                       ("Environment & Survey", "Coastal, watershed, and terrain imagery"),
                       ("Heritage & Culture", "Buildings, libraries, and cultural assets")]:
        existing = conn.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
        if existing:
            # Re-running the seed reuses collections instead of stacking copies.
            cols[name] = existing["id"]
            continue
        cur = conn.execute(
            "INSERT INTO collections (uuid, name, description, kind, created_by, created_at) VALUES (?,?,?, 'folder', ?, ?)",
            (str(uuidlib.uuid4()), name, desc, admin["id"], now_iso()))
        cols[name] = cur.lastrowid
    conn.commit()
    # distribute assets across collections by tag affinity
    for aid in created:
        row = conn.execute(
            "SELECT GROUP_CONCAT(t.name) g FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=?",
            (aid,)).fetchone()
        g = (row["g"] or "")
        target = None
        if any(k in g for k in ("civic", "poster", "notice", "event", "logo", "transit", "roads")):
            target = cols["Civic Communications"]
        elif any(k in g for k in ("environment", "coast", "water", "survey", "terrain", "outdoors")):
            target = cols["Environment & Survey"]
        elif any(k in g for k in ("heritage", "history", "library", "culture")):
            target = cols["Heritage & Culture"]
        if target:
            conn.execute("INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) VALUES (?,?,?)",
                         (target, aid, now_iso()))
    conn.commit()

    print(f"Seeded {len(created)} assets, {len(cols)} collections, and demo users.")
    if with_demo:
        demo_ids = _seed_campaign(conn, admin)
        print(f"Demo dataset: {len(demo_ids)} campaign assets in collection 'Harbor Campaign'.")
    print("Sign in as admin/admin (also: curator, contributor, viewer / demo1234).")


def _seed_campaign(conn, admin: dict) -> list[int]:
    """Append the dataset used by the review scenario.

    Scenario: inside the "Harbor Campaign" collection, search "harbor", keep
    assets tagged approved AND campaign, drop anything tagged internal.
    28 assets, 5 share each timestamp, so 12 per page gives two pages and a
    tie group that crosses the page boundary.
    """
    import uuid as uuidlib

    # Re-running --demo must not stack duplicate collections.
    existing = conn.execute(
        "SELECT id FROM collections WHERE name = 'Harbor Campaign'").fetchone()
    if existing:
        campaign_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO collections (uuid, name, description, kind, created_by, created_at) "
            "VALUES (?,?,?, 'folder', ?, ?)",
            (str(uuidlib.uuid4()), "Harbor Campaign",
             "Launch assets for the harbor campaign scenario", admin["id"], _stamp(90)))
        campaign_id = cur.lastrowid
        conn.commit()

    total = 28
    ids: list[int] = []
    for i in range(total):
        label = f"Harbor campaign {i + 1:02d}"
        if i >= total - 2:
            # Two documents prove the media_type filter also works on export.
            body = (f"{label}\nBrief, with comma and \"quotes\".\n第二行中文。\n").encode("utf-8")
            asset = ingest.ingest_stream(io.BytesIO(body), f"harbor_campaign_{i + 1:02d}.txt",
                                         user=admin, run_ai=False, title=label)
        else:
            asset = ingest.ingest_stream(io.BytesIO(_make_image(40 + i, label)),
                                         f"harbor_campaign_{i + 1:02d}.jpg",
                                         user=admin, run_ai=True, title=label)

        names = ["harbor", "campaign", "approved", "public"]
        if i % 4 == 3:
            names.append("internal")
        if i % 9 == 4:
            names.remove("approved")
        if i % 9 == 7:
            names.remove("campaign")
        _add_tags(conn, asset["id"], names)
        conn.execute(
            "INSERT OR IGNORE INTO collection_assets (collection_id, asset_id, added_at) VALUES (?,?,?)",
            (campaign_id, asset["id"], _stamp(100)))
        conn.commit()
        _set_created_at(conn, asset["id"], _stamp(100 + i // 5))
        ingest.reindex_fts(asset["id"])
        ingest.reindex_embedding(asset["id"])
        ids.append(asset["id"])

    _set_description(conn, ids[0], 'Campaign hero，中文描述，"引号"、逗号都在这里。')
    _set_description(conn, ids[1], 'Line one\n第二行，带逗号\nthird "quoted" line')
    return ids


if __name__ == "__main__":
    run_seed()
