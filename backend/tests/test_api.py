"""The HTTP layer. Thin by design, so these tests check plumbing and the two
things the API is actually responsible for: serving a renderable trace, and
refusing to invent a send path."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from intake.api.main import app, get_conn
from intake.db import repo
from intake.db.seed import build_corpus, seed_database
from intake.llm.client import ResponseCache
from intake.paths import LLM_CACHE_PATH
from intake.pipeline.process import process_inbox


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """A temp database, so posting reviews never touches the checked-in one."""
    if not LLM_CACHE_PATH.exists() or not len(ResponseCache(LLM_CACHE_PATH)):
        pytest.skip("no response cache checked in; run `make cache` with an API key")

    db_path = tmp_path_factory.mktemp("api") / "intake.sqlite3"
    seed_database(build_corpus(20260517, verbose=False), db_path)
    process_inbox(db_path, provider_mode="replay", verbose=False)

    def override():
        conn = repo.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_conn] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def test_health_reports_the_corpus_seed(client) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["emails"] == 51
    assert body["runs"] == 51


def test_the_inbox_lists_every_email_with_its_decision(client) -> None:
    body = client.get("/api/inbox").json()
    assert len(body["items"]) == 51
    assert body["counts"]["total"] == 51
    assert body["counts"]["proceed"] + body["counts"]["review"] + body["counts"]["stop"] == 51
    assert all(item["action"] in ("proceed", "review", "stop") for item in body["items"])


def test_the_inbox_can_be_filtered_to_the_review_queue(client) -> None:
    body = client.get("/api/inbox", params={"action": "review", "pending_only": True}).json()
    assert body["items"]
    assert all(i["action"] == "review" for i in body["items"])
    assert all(not i["reviewed"] for i in body["items"])
    assert all(i["reason_count"] >= 1 for i in body["items"]), (
        "an item in the review queue must say why it is there"
    )


def test_spans_index_into_the_served_source_text(client) -> None:
    """The one thing the API layer genuinely owns. Offsets are computed against
    Email.searchable_text, which is not the body -- serving the body alone would
    make every highlight in the UI land a few hundred characters off."""
    checked = 0
    for item in client.get("/api/inbox").json()["items"]:
        detail = client.get(f"/api/emails/{item['email_id']}").json()
        source = detail["source_text"]
        extraction = (detail.get("result") or {}).get("extraction")
        if not extraction:
            continue
        for party in extraction["parties"]:
            span = party["name"].get("span")
            if span and span["status"] in ("verified", "normalized"):
                assert source[span["start"] : span["end"]].lower() == span["quote"].lower()
                checked += 1
    assert checked > 20, "expected plenty of grounded spans to verify against"


def test_thresholds_are_served_so_the_ui_can_show_the_bar(client) -> None:
    body = client.get("/api/thresholds").json()
    assert 0 < body["classification"] <= 1
    assert body["party_name"] >= body["matter_type"], (
        "names gate the conflicts check and are held higher than routing fields"
    )


def test_unknown_email_is_a_404(client) -> None:
    assert client.get("/api/emails/em-nope").status_code == 404


# --------------------------------------------------------------------------- #
# Reviewing
# --------------------------------------------------------------------------- #


def test_approving_marks_the_draft_approved_but_not_sent(client) -> None:
    queue = client.get("/api/inbox", params={"action": "review", "pending_only": True}).json()
    target = next(
        i for i in queue["items"]
        if client.get(f"/api/emails/{i['email_id']}").json()["result"]["dispatch"]["acknowledgment"]
    )
    body = client.post(
        f"/api/emails/{target['email_id']}/review",
        json={"outcome": "approved", "note": "checked the conflict, cleared"},
    ).json()
    assert body["result"]["dispatch"]["acknowledgment"]["status"] == "approved_not_sent"
    assert body["result"]["review"]["outcome"] == "approved"
    assert len(body["review_log"]) == 1


def test_a_correction_is_applied_and_the_original_kept(client) -> None:
    detail = client.get("/api/emails/em-003").json()
    original = detail["result"]["extraction"]["jurisdiction"]["value"]
    body = client.post(
        "/api/emails/em-003/review",
        json={
            "outcome": "approved_with_edits",
            "note": "jurisdiction was wrong",
            "edits": [{
                "field_path": "extraction.jurisdiction",
                "original_value": original,
                "corrected_value": "Cook County, Illinois",
            }],
        },
    ).json()
    field = body["result"]["extraction"]["jurisdiction"]
    assert field["value"] == "Cook County, Illinois"
    assert field["confidence"] == 1.0
    assert field["span"] is None, "a human's correction is not quoted from the email"
    assert original in field["validator_note"]


def test_an_uneditable_field_is_refused(client) -> None:
    response = client.post(
        "/api/emails/em-006/review",
        json={
            "outcome": "approved_with_edits",
            "edits": [{"field_path": "decision.action", "corrected_value": "proceed"}],
        },
    )
    assert response.status_code == 422


def test_reviewing_an_unprocessed_email_is_a_404(client) -> None:
    assert client.post("/api/emails/em-nope/review", json={"outcome": "approved"}).status_code == 404


def test_the_review_log_is_append_only_across_requests(client) -> None:
    for note in ("first pass", "second look"):
        client.post("/api/emails/em-015/review", json={"outcome": "rejected", "note": note})
    log = client.get("/api/emails/em-015").json()["review_log"]
    assert [entry["note"] for entry in log] == ["first pass", "second look"]


# --------------------------------------------------------------------------- #
# The absent feature
# --------------------------------------------------------------------------- #


def test_there_is_no_route_that_sends_anything(client) -> None:
    """The system's central promise, asserted against the actual route table
    rather than trusted."""
    paths = [route.path for route in app.routes]
    assert not any("send" in p or "deliver" in p or "email/send" in p for p in paths)
    methods = {
        (route.path, m) for route in app.routes for m in getattr(route, "methods", set())
    }
    mutating = {p for p, m in methods if m in {"POST", "PUT", "PATCH", "DELETE"}}
    assert mutating == {"/api/emails/{email_id}/review"}, (
        f"the only write in this API should be a review; found {mutating}"
    )


# --------------------------------------------------------------------------- #
# Serving the built front end
# --------------------------------------------------------------------------- #


def test_the_spa_fallback_never_shadows_an_api_route(tmp_path) -> None:
    """A mis-ordered catch-all turns a 404 from the API into a 200 of index.html,
    and the front end then tries to parse HTML as JSON. Registration order is the
    only thing preventing that, so it is asserted rather than assumed.

    Built on a fresh app: the real one mounts its own dist at import time when a
    build is present, and two mounts on /assets would test the wrong one.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient as Client

    from intake.api.main import mount_frontend

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>built</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")

    application = FastAPI()

    @application.get("/api/thing/{name}")
    def thing(name: str):
        if name != "real":
            raise HTTPException(status_code=404, detail="no such thing")
        return {"ok": True}

    assert mount_frontend(application, dist) is True
    client = Client(application)

    assert client.get("/").text.startswith("<!doctype html>")
    assert client.get("/queue/em-003").text.startswith("<!doctype html>"), "client routes"
    assert client.get("/assets/app.js").status_code == 200

    assert client.get("/api/thing/real").json() == {"ok": True}
    assert client.get("/api/thing/missing").status_code == 404, (
        "the catch-all must not turn an API 404 into an index.html 200"
    )


def test_the_api_runs_without_a_front_end_build(tmp_path) -> None:
    """Development, and any deployment that serves the front end elsewhere."""
    from fastapi import FastAPI

    from intake.api.main import mount_frontend

    assert mount_frontend(FastAPI(), tmp_path / "never-built") is False
