"""CSV export of the current library query.

The export reuses ``search.build_filters`` so the file always matches the
list page: same keyword, media type, collection, include/exclude tags and
same sort order. Only the paging is dropped.

Format notes:
  * UTF-8 with BOM, so Excel on Windows detects the encoding.
  * CRLF line endings and ``csv.writer`` quoting: commas, double quotes and
    newlines inside a field survive a round trip through ``csv.reader``.
  * Tags are joined with ``|`` in name order.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi.responses import StreamingResponse

from . import search

COLUMNS = ["uuid", "title", "description", "filename", "media_type", "tags", "created_at"]
TAG_SEPARATOR = "|"
CHUNK_ROWS = 200


def _flush(buf: io.StringIO) -> str:
    value = buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    return value


def iter_csv(where_sql: str, params: list, order_sql: str):
    """Yield BOM + header first, then rows in chunks of CHUNK_ROWS.

    Zero matches still yield the header, which is what the task requires.
    """
    cursor = search.iter_matching(where_sql, params, order_sql)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    yield "\ufeff" + _flush(buf)

    while True:
        rows = cursor.fetchmany(CHUNK_ROWS)
        if not rows:
            break
        tag_map = search.tags_for([row["id"] for row in rows])
        for row in rows:
            writer.writerow([
                row["uuid"] or "",
                row["title"] or "",
                row["description"] or "",
                row["filename"] or "",
                row["media_type"] or "",
                TAG_SEPARATOR.join(tag_map.get(row["id"], [])),
                row["created_at"] or "",
            ])
        yield _flush(buf)


def content_disposition(now: datetime | None = None) -> str:
    """ASCII fallback plus RFC 5987 UTF-8 name (headers must stay latin-1)."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    ascii_name = f"keepstack-assets-{stamp}.csv"
    utf8_name = quote(f"素材清单-{stamp}.csv")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"


def csv_response(where_sql: str, params: list, order_sql: str) -> StreamingResponse:
    return StreamingResponse(
        iter_csv(where_sql, params, order_sql),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition()},
    )
