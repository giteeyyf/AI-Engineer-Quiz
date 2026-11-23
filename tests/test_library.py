"""Behaviour tests for combined filters, stable paging and CSV export."""
from __future__ import annotations

import csv
import io

CSV_HEADER = ["uuid", "title", "description", "filename", "media_type", "tags", "created_at"]


def _uuid_list(data: dict) -> list[str]:
    return [item["uuid"] for item in data["items"]]


def _list(client, params=None, **overrides):
    query = dict(params or {})
    query.update(overrides)
    return client.get("/api/assets", params=query)


def _all_items(client, page_size=2, **params):
    """Walk every page and return the concatenated items in order."""
    items, page = [], 1
    while True:
        data = _list(client, params, page=page, page_size=page_size).json()
        items.extend(data["items"])
        if not data["pages"] or page >= data["pages"]:
            return items, data
        page += 1


def _export(client, **params):
    return client.get("/api/assets/export.csv", params=params)


def _csv_rows(response) -> list[list[str]]:
    body = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(body, newline="")))


def test_combined_filters_intersect(client, make_asset, make_collection):
    harbor = make_collection("Harbor Collection")
    elsewhere = make_collection("Elsewhere")
    make_asset(uuid="hit", title="Harbor view", tags=["approved", "campaign"],
               collection_id=harbor)
    make_asset(uuid="no-campaign", title="Harbor view two", tags=["approved"],
               collection_id=harbor)
    make_asset(uuid="internal", title="Harbor view three", tags=["approved", "campaign", "internal"],
               collection_id=harbor)
    make_asset(uuid="other-collection", title="Harbor view four", tags=["approved", "campaign"],
               collection_id=elsewhere)
    make_asset(uuid="deleted", title="Harbor view five", tags=["approved", "campaign"],
               collection_id=harbor, status="deleted")
    make_asset(uuid="no-keyword", title="Lighthouse", tags=["approved", "campaign"],
               collection_id=harbor)

    data = _list(client, q="harbor", collection_id=harbor,
                 tags=["approved", "campaign"], exclude_tags=["internal"]).json()

    assert data["total"] == 1
    assert _uuid_list(data) == ["hit"]
    assert data["applied"]["include_tags"] == ["approved", "campaign"]
    assert data["applied"]["exclude_tags"] == ["internal"]
    assert data["applied"]["collection_name"] == "Harbor Collection"


def test_duplicate_tags_do_not_change_results(client, make_asset):
    make_asset(uuid="one", title="Harbor one", tags=["approved", "campaign"])
    make_asset(uuid="two", title="Harbor two", tags=["approved"])

    once = _list(client, tags=["approved", "campaign"]).json()
    twice = _list(client, tags=["approved", "approved", "campaign"]).json()

    assert once["total"] == twice["total"] == 1
    assert _uuid_list(once) == _uuid_list(twice)


def test_include_and_exclude_conflict_is_empty(client, make_asset):
    make_asset(uuid="one", title="Harbor one", tags=["approved", "internal"])

    data = _list(client, tags=["approved"], exclude_tags=["Approved"]).json()
    assert data["total"] == 0
    assert data["items"] == []

    exported = _csv_rows(_export(client, tags=["approved"], exclude_tags=["approved"]))
    assert exported == [CSV_HEADER]


def test_only_active_assets_are_returned(client, make_asset):
    make_asset(uuid="active", title="Harbor active", tags=["approved", "campaign"])
    make_asset(uuid="gone", title="Harbor deleted", tags=["approved", "campaign"], status="deleted")

    data = _list(client, q="harbor").json()
    assert _uuid_list(data) == ["active"]

    # The legacy status parameter cannot widen the result set.
    forced = _list(client, q="harbor", status="deleted").json()
    assert forced["total"] == 1


def test_pages_partition_the_result_set_without_overlap(client, make_asset):
    for i in range(7):
        make_asset(uuid=f"asset-{i}", title=f"Harbor {i}",
                   created_at=f"2026-03-01T10:0{i}:00.000000+00:00")

    items, data = _all_items(client, page_size=3)
    ids = [item["uuid"] for item in items]

    assert data["total"] == 7 and data["pages"] == 3
    assert len(ids) == len(set(ids)) == 7
    assert ids == [f"asset-{i}" for i in range(6, -1, -1)]

    oldest = _list(client, sort="oldest", page=1, page_size=100).json()
    assert _uuid_list(oldest) == list(reversed(ids))


def test_same_created_at_stays_stable_across_pages(client, make_asset):
    stamp = "2026-04-01T00:00:00.000000+00:00"
    for i in range(5):
        make_asset(uuid=f"tie-{i}", title=f"Harbor tie {i}", created_at=stamp)

    items, data = _all_items(client, page_size=2)
    ids = [item["uuid"] for item in items]

    assert data["total"] == 5 and data["pages"] == 3
    assert ids == ["tie-4", "tie-3", "tie-2", "tie-1", "tie-0"]
    assert len(ids) == len(set(ids)) == 5


def test_keyword_keeps_the_requested_sort(client, make_asset):
    for i, stamp in enumerate(["2026-02-01T00:00:00.000000+00:00",
                               "2026-02-02T00:00:00.000000+00:00",
                               "2026-02-03T00:00:00.000000+00:00"]):
        make_asset(uuid=f"k-{i}", title=f"Harbor keyword {i}", created_at=stamp)

    oldest = _list(client, q="harbor", sort="oldest", page_size=10).json()
    newest = _list(client, q="harbor", sort="newest", page_size=10).json()

    assert [item["created_at"] for item in oldest["items"]] == sorted(
        item["created_at"] for item in oldest["items"])
    assert [item["created_at"] for item in newest["items"]] == sorted(
        (item["created_at"] for item in newest["items"]), reverse=True)


def test_invalid_paging_is_rejected_and_past_end_is_empty(client, make_asset):
    make_asset(uuid="one", title="Harbor one")

    assert client.get("/api/assets", params={"page": 0}).status_code == 422
    assert client.get("/api/assets", params={"page_size": 0}).status_code == 422
    assert client.get("/api/assets", params={"page_size": 101}).status_code == 422
    assert client.get("/api/assets", params={"page": "abc"}).status_code == 422

    far = _list(client, page=99, page_size=12)
    assert far.status_code == 200
    assert far.json()["items"] == []
    assert far.json()["total"] == 1
    assert far.json()["page"] == 99


def test_special_characters_never_break_search(client, make_asset):
    make_asset(uuid="one", title="Harbor one")

    assert _list(client, q="!!!").status_code == 200
    assert _list(client, q="!!!").json()["total"] == 0
    assert _list(client, q="😀").json()["total"] == 0
    assert _list(client, q="100%").status_code == 200
    assert _list(client, q="a-b").status_code == 200
    assert _list(client, q="   ").json()["total"] == 1


def test_export_matches_list_order_and_round_trips_fields(client, make_asset):
    tricky = '中文，逗号与"引号"\n第二行'
    make_asset(uuid="first", title="Harbor first", description=tricky,
               tags=["approved", "campaign"],
               created_at="2026-05-01T00:00:00.000000+00:00")
    make_asset(uuid="second", title="Harbor second", description="plain",
               tags=["approved", "campaign"],
               created_at="2026-05-02T00:00:00.000000+00:00")
    make_asset(uuid="third", title="Harbor third", tags=["approved", "campaign"],
               created_at="2026-05-03T00:00:00.000000+00:00")
    make_asset(uuid="internal", title="Harbor internal", tags=["approved", "campaign", "internal"],
               created_at="2026-05-04T00:00:00.000000+00:00")

    params = {"q": "harbor", "tags": ["approved", "campaign"],
              "exclude_tags": ["internal"], "sort": "oldest"}
    listed, _ = _all_items(client, page_size=2, **params)

    response = _export(client, **params)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in response.content

    rows = _csv_rows(response)
    assert rows[0] == CSV_HEADER
    assert [row[0] for row in rows[1:]] == [item["uuid"] for item in listed]
    assert len(rows) - 1 == 3
    assert rows[1][1] == "Harbor first"
    assert rows[1][2] == tricky
    assert rows[1][5] == "approved|campaign"


def test_export_zero_matches_still_has_header(client):
    rows = _csv_rows(_export(client, q="nothing-matches-this"))
    assert rows == [CSV_HEADER]
    assert _export(client, q="nothing-matches-this").status_code == 200


def test_edits_are_reflected_in_the_next_query_and_export(client, make_asset):
    make_asset(uuid="edit-me", title="Old Harbor", description="before",
               tags=["approved"])

    patched = client.patch("/api/assets/edit-me", json={
        "title": "Unique Needle", "description": "after，中文"})
    assert patched.status_code == 200

    assert _list(client, q="needle").json()["total"] == 1
    assert _list(client, q="harbor").json()["total"] == 0

    client.post("/api/assets/edit-me/tags", json={"name": "campaign"})
    filtered = _list(client, tags=["approved", "campaign"]).json()
    assert _uuid_list(filtered) == ["edit-me"]

    rows = _csv_rows(_export(client, tags=["approved", "campaign"]))
    assert rows[1][1] == "Unique Needle"
    assert rows[1][2] == "after，中文"
    assert rows[1][5] == "approved|campaign"

    client.delete("/api/assets/edit-me/tags/campaign")
    assert _list(client, tags=["approved", "campaign"]).json()["total"] == 0


def test_export_requires_login(anon_client):
    assert anon_client.get("/api/assets/export.csv").status_code == 401
