"""Search and discovery.

Three complementary modes over one catalog:

  * keyword  - FTS5 full-text across title, description, tags, metadata
  * filter   - structured filters (media type, collection, include/exclude tags)
  * semantic - embedding cosine ranking ("natural language" / more-like-this)

Keyword, filter, count and CSV export all share one query builder
(``build_filters``) so the three consumers can never drift apart.

Facet counts are returned alongside results so the UI can render a live,
drill-down sidebar. Facets are catalog-wide statistics; they are not the
current match count.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from . import ai
from .db import get_conn

# First sort key plus a unique tie-breaker. The tie-breaker points the same
# way as the primary key so ascending and descending are strict reverses.
SORTS = {
    "newest": ("a.created_at DESC", "a.id DESC"),
    "oldest": ("a.created_at ASC", "a.id ASC"),
    "name": ("a.title ASC", "a.id ASC"),
    "largest": ("a.size DESC", "a.id DESC"),
}
DEFAULT_PAGE_SIZE = 12
MAX_PAGE_SIZE = 100


def normalize_tags(values: Optional[Iterable[str]]) -> list[str]:
    """Trim, lower-case, drop empties and de-duplicate while keeping order.

    Tag writes store lower-case names, so filtering uses the same casing.
    Duplicate inputs collapse here, which keeps "must contain all" counts sane.
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values or ():
        name = (value or "").strip().lower()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query.

    Anything that is not alphanumeric becomes a space, so quotes, parens and
    operators cannot reach the MATCH parser. Returns "" when no term survives
    (for example "!!!"), which callers treat as "nothing can match".
    """
    terms = [t for t in "".join(c if c.isalnum() else " " for c in q).split() if t]
    if not terms:
        return ""
    return " ".join(f'"{t}"*' for t in terms)


def order_clause(sort: str) -> str:
    primary, tie = SORTS.get(sort, SORTS["newest"])
    return f"ORDER BY {primary}, {tie}"


def build_filters(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    collection_id: Optional[int] = None,
    tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    sort: str = "newest",
) -> tuple[str, list, str]:
    """Build one WHERE clause shared by count, page listing and CSV export.

    Every condition is an EXISTS/IN subquery on ``assets a``, so each asset is
    exactly one row. That keeps ``COUNT(*)``, ``LIMIT/OFFSET`` and export rows
    structurally identical.
    """
    where = ["a.status = 'active'"]
    params: list = []

    text = (q or "").strip()
    if text:
        match = _fts_query(text)
        if match:
            where.append("a.id IN (SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?)")
            params.append(match)
        else:
            # The user typed something, but nothing searchable survived
            # sanitising. Return no rows instead of widening to the whole
            # catalog, and never pass an empty string to MATCH.
            where.append("0")

    if media_type:
        where.append("a.media_type = ?")
        params.append(media_type)

    if collection_id:
        where.append("a.id IN (SELECT asset_id FROM collection_assets WHERE collection_id = ?)")
        params.append(collection_id)

    include = normalize_tags(tags)
    exclude = normalize_tags(exclude_tags)

    # The same tag on both sides can never be satisfied.
    if set(include) & set(exclude):
        return "WHERE 0", [], order_clause(sort)

    for name in include:
        where.append(
            "EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            "WHERE at.asset_id = a.id AND t.name = ?)"
        )
        params.append(name)

    if exclude:
        marks = ",".join("?" * len(exclude))
        where.append(
            "NOT EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
            f"WHERE at.asset_id = a.id AND t.name IN ({marks}))"
        )
        params.extend(exclude)

    return "WHERE " + " AND ".join(where), params, order_clause(sort)


def resolve_paging(
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    limit: int = 60,
    offset: int = 0,
) -> tuple[int, int, int]:
    """Return (page, page_size, offset).

    ``page/page_size`` is the public contract. ``limit/offset`` stays as a
    compatibility path for older clients and is converted to a page number.
    """
    if page is None:
        size = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
        start = max(0, int(offset or 0))
        return start // size + 1, size, start
    size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    number = max(1, int(page))
    return number, size, (number - 1) * size


def count_matching(where_sql: str, params: list) -> int:
    conn = get_conn()
    return conn.execute(
        f"SELECT COUNT(*) AS c FROM assets a {where_sql}", params
    ).fetchone()["c"]


def fetch_rows(where_sql: str, params: list, order_sql: str,
               limit: int, offset: int) -> list:
    conn = get_conn()
    return conn.execute(
        f"SELECT a.* FROM assets a {where_sql} {order_sql} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()


def iter_matching(where_sql: str, params: list, order_sql: str):
    """Cursor over every match, used by the CSV export (no LIMIT)."""
    conn = get_conn()
    return conn.execute(
        f"SELECT a.* FROM assets a {where_sql} {order_sql}", params
    )


def tags_for(asset_ids: Iterable[int]) -> dict[int, list[str]]:
    """Bulk-load tag names for a page of assets (avoids N+1 queries)."""
    ids = list(asset_ids)
    out: dict[int, list[str]] = {}
    if not ids:
        return out
    conn = get_conn()
    # SQLite's default host-parameter limit is 999; 500 keeps queries small.
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            "SELECT at.asset_id AS id, t.name AS name FROM asset_tags at "
            "JOIN tags t ON t.id = at.tag_id "
            f"WHERE at.asset_id IN ({marks}) ORDER BY at.asset_id, t.name",
            chunk,
        ).fetchall()
        for row in rows:
            out.setdefault(row["id"], []).append(row["name"])
    return out


def collection_name(collection_id: Optional[int]) -> Optional[str]:
    if not collection_id:
        return None
    row = get_conn().execute(
        "SELECT name FROM collections WHERE id = ?", (collection_id,)
    ).fetchone()
    return row["name"] if row else None


def applied_filters(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    collection_id: Optional[int] = None,
    tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    sort: str = "newest",
) -> dict:
    """Echo of the conditions actually executed, rendered by the UI chips."""
    return {
        "q": (q or "").strip(),
        "media_type": media_type,
        "collection_id": collection_id,
        "collection_name": collection_name(collection_id),
        "include_tags": normalize_tags(tags),
        "exclude_tags": normalize_tags(exclude_tags),
        "sort": sort if sort in SORTS else "newest",
    }


def search(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    exclude_tags: Optional[Iterable[str]] = None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: str = "newest",
    mode: str = "keyword",
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    limit: int = 60,
    offset: int = 0,
) -> dict:
    page_no, size, start = resolve_paging(page, page_size, limit, offset)

    # ``tag`` is the legacy single-tag parameter; merge it into the new list.
    include = list(tags or ())
    if tag:
        include.append(tag)

    # Semantic mode ranks the candidate pool by embedding similarity.
    if mode == "semantic" and q.strip():
        result = _semantic_search(q, media_type, status, size, start)
        result.update({
            "page": page_no,
            "page_size": size,
            "pages": math.ceil(result["total"] / size) if result["total"] else 0,
            "applied": applied_filters(q=q, media_type=media_type,
                                       collection_id=collection_id, tags=include,
                                       exclude_tags=exclude_tags, sort=sort),
        })
        return result

    where_sql, params, order_sql = build_filters(
        q=q, media_type=media_type, collection_id=collection_id,
        tags=include, exclude_tags=exclude_tags, sort=sort,
    )
    total = count_matching(where_sql, params)
    rows = fetch_rows(where_sql, params, order_sql, size, start)
    tag_map = tags_for([r["id"] for r in rows])
    items = [_card(dict(r), tag_map.get(r["id"], [])) for r in rows]

    return {
        "total": total,
        "page": page_no,
        "page_size": size,
        "pages": math.ceil(total / size) if total else 0,
        "sort": sort if sort in SORTS else "newest",
        "mode": mode,
        "applied": applied_filters(q=q, media_type=media_type,
                                   collection_id=collection_id, tags=include,
                                   exclude_tags=exclude_tags, sort=sort),
        "items": items,
        "facets": _facets("active"),
    }


def _semantic_search(q, media_type, status, limit, offset) -> dict:
    conn = get_conn()
    qvec = ai.embed(q)
    where = ["status = ?", "embedding IS NOT NULL"]
    params: list = [status]
    if media_type:
        where.append("media_type = ?")
        params.append(media_type)
    rows = conn.execute(
        f"SELECT * FROM assets WHERE {' AND '.join(where)}", params
    ).fetchall()
    scored = []
    for r in rows:
        score = ai.cosine(qvec, ai.unpack_embedding(r["embedding"]))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    page = scored[offset:offset + limit]
    items = []
    for score, r in page:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        items.append(card)
    return {"total": len(scored), "items": items, "facets": _facets(status), "mode": "semantic"}


def more_like(asset_id: int, limit: int = 12) -> list[dict]:
    conn = get_conn()
    base = conn.execute("SELECT embedding FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not base or not base["embedding"]:
        return []
    bvec = ai.unpack_embedding(base["embedding"])
    rows = conn.execute(
        "SELECT * FROM assets WHERE id != ? AND status = 'active' AND embedding IS NOT NULL",
        (asset_id,),
    ).fetchall()
    scored = [(ai.cosine(bvec, ai.unpack_embedding(r["embedding"])), r) for r in rows]
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, r in scored[:limit]:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        out.append(card)
    return out


def _facets(status: str) -> dict:
    conn = get_conn()
    media = conn.execute(
        "SELECT media_type, COUNT(*) c FROM assets WHERE status = ? GROUP BY media_type ORDER BY c DESC",
        (status,),
    ).fetchall()
    tags = conn.execute(
        """SELECT t.name, COUNT(*) c FROM tags t
           JOIN asset_tags at ON at.tag_id = t.id
           JOIN assets a ON a.id = at.asset_id AND a.status = ?
           GROUP BY t.name ORDER BY c DESC LIMIT 25""",
        (status,),
    ).fetchall()
    return {
        "media_types": [{"value": r["media_type"], "count": r["c"]} for r in media],
        "tags": [{"value": r["name"], "count": r["c"]} for r in tags],
    }


def _card(a: dict, tags: Optional[list[str]] = None) -> dict:
    """Compact asset representation for grid/list views."""
    return {
        "id": a["id"],
        "uuid": a["uuid"],
        "title": a["title"],
        "description": a["description"],
        "filename": a["filename"],
        "media_type": a["media_type"],
        "mime_type": a["mime_type"],
        "ext": a["ext"],
        "size": a["size"],
        "width": a["width"],
        "height": a["height"],
        "alt_text": a["alt_text"],
        "status": a["status"],
        "tags": tags or [],
        "created_at": a["created_at"],
    }
