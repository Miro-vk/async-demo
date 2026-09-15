"""HTTP layer.

Thin on purpose. Every route reads from the database or calls a domain function;
none of them decide anything. The thresholds live in domain.policy, the conflict
rules in domain.conflicts, and if a rule ever appears in this file it is in the
wrong place.

The one thing this layer does own is making the trace renderable: `source_text` is
served alongside every result so the front end highlights spans against exactly the
string the offsets were computed from.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from intake.db import repo
from intake.domain.enums import DecisionAction, ReviewOutcome
from intake.domain.models import FieldEdit, PipelineResult, ReviewAction
from intake.domain.policy import DEFAULT as DEFAULT_THRESHOLDS
from intake.domain.review import UneditableField, apply_review
from intake.paths import DATABASE_PATH, FRONTEND_DIST

# The demo has no auth and no users. Actions are attributed to a single reviewer so
# the audit trail has a name in it; a real system would take this from a session.
DEMO_REVIEWER = "intake@vancebrock.example"

# Fixed so repeated demo runs produce identical audit rows.
REVIEW_CLOCK = datetime(2026, 3, 2, 12, 0, 0)

app = FastAPI(
    title="Client intake triage",
    description="Synthetic demo. No real email, no auth, nothing is ever sent.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn():
    conn = repo.connect(DATABASE_PATH)
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #


class InboxItem(BaseModel):
    email_id: str
    received_at: datetime
    from_name: str
    from_email: str
    subject: str
    preview: str
    label: str | None
    label_confidence: float | None
    action: str | None
    reason_count: int
    top_reason: str | None
    conflict_severity: str | None
    reviewed: bool
    attorney_id: str | None


class Counts(BaseModel):
    total: int
    proceed: int
    review: int
    stop: int
    pending_review: int
    reviewed: int


class InboxResponse(BaseModel):
    items: list[InboxItem]
    counts: Counts


class EmailDetail(BaseModel):
    email_id: str
    received_at: datetime
    from_name: str
    from_email: str
    to_address: str
    subject: str
    body: str
    source_text: str = Field(
        description=(
            "The exact string every span offset indexes into. Highlight against "
            "this, not against body -- they differ by the header block."
        )
    )
    prose_source: str
    result: PipelineResult | None
    attorney: dict[str, Any] | None
    matched_clients: list[dict[str, Any]] = []
    review_log: list[ReviewAction] = []


class EditRequest(BaseModel):
    field_path: str
    original_value: str | None = None
    corrected_value: str | None = None


class ReviewRequest(BaseModel):
    outcome: Literal["approved", "approved_with_edits", "rejected"]
    note: str = ""
    reviewer: str = DEMO_REVIEWER
    edits: list[EditRequest] = []


class ThresholdsResponse(BaseModel):
    classification: float
    party_name: float
    matter_type: float
    conflict_floor: str


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.get("/api/health")
def health(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return {
        "ok": True,
        "emails": len(repo.list_emails(conn)),
        "runs": len(repo.list_runs(conn)),
        "corpus_seed": repo.get_meta(conn, "corpus_seed"),
        "note": "Synthetic data. Nothing here is ever sent to anyone.",
    }


@app.get("/api/thresholds", response_model=ThresholdsResponse)
def thresholds() -> ThresholdsResponse:
    """Served so the UI can show the bar a field failed, next to the field."""
    return ThresholdsResponse(
        classification=DEFAULT_THRESHOLDS.classification,
        party_name=DEFAULT_THRESHOLDS.party_name,
        matter_type=DEFAULT_THRESHOLDS.matter_type,
        conflict_floor=DEFAULT_THRESHOLDS.conflict_floor.value,
    )


@app.get("/api/inbox", response_model=InboxResponse)
def inbox(
    action: str | None = None,
    pending_only: bool = False,
    conn: sqlite3.Connection = Depends(get_conn),
) -> InboxResponse:
    emails = {e.id: e for e in repo.list_emails(conn)}
    runs = {r.email_id: r for r in repo.list_runs(conn)}

    wanted = DecisionAction(action) if action else None
    items: list[InboxItem] = []
    for email in sorted(emails.values(), key=lambda e: e.received_at, reverse=True):
        result = runs.get(email.id)
        if wanted and (result is None or result.decision.action != wanted):
            continue
        if pending_only and (result is None or not result.awaiting_review):
            continue
        items.append(_inbox_item(email, result))

    counts = repo.queue_counts(conn)
    return InboxResponse(
        items=items,
        counts=Counts(
            total=len(emails),
            proceed=counts.get("proceed", 0) + counts.get("proceed_pending", 0),
            review=counts.get("review", 0) + counts.get("review_pending", 0),
            stop=counts.get("stop", 0) + counts.get("stop_pending", 0),
            pending_review=counts.get("review_pending", 0),
            reviewed=sum(v for k, v in counts.items() if not k.endswith("_pending")),
        ),
    )


def _inbox_item(email, result: PipelineResult | None) -> InboxItem:
    label = conf = action = top = severity = attorney = None
    reason_count = 0
    reviewed = False

    if result is not None:
        action = result.decision.action.value
        reason_count = len(result.decision.reasons)
        top = result.decision.reasons[0].code.value if result.decision.reasons else None
        reviewed = result.review is not None
        attorney = result.dispatch.attorney_id if result.dispatch else None
        if result.classification:
            label = result.classification.label.value.value
            conf = result.classification.label.confidence
        if result.resolution and result.resolution.conflicts:
            severity = result.resolution.max_severity.value

    body = " ".join(email.body.split())
    return InboxItem(
        email_id=email.id,
        received_at=email.received_at,
        from_name=email.from_name,
        from_email=email.from_email,
        subject=email.subject,
        preview=body[:160] + ("..." if len(body) > 160 else ""),
        label=label,
        label_confidence=conf,
        action=action,
        reason_count=reason_count,
        top_reason=top,
        conflict_severity=severity,
        reviewed=reviewed,
        attorney_id=attorney,
    )


@app.get("/api/emails/{email_id}", response_model=EmailDetail)
def email_detail(
    email_id: str, conn: sqlite3.Connection = Depends(get_conn)
) -> EmailDetail:
    email = repo.get_email(conn, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail=f"no email {email_id}")

    result = repo.get_run(conn, email_id)
    attorney = None
    if result and result.dispatch and result.dispatch.attorney_id:
        match = next(
            (a for a in repo.list_attorneys(conn) if a.id == result.dispatch.attorney_id), None
        )
        attorney = match.model_dump(mode="json") if match else None

    matched: list[dict[str, Any]] = []
    if result and result.resolution:
        clients = {c.id: c for c in repo.list_clients(conn)}
        for m in result.resolution.client_matches:
            client = clients.get(m.client_id)
            if client:
                matched.append(
                    {**m.model_dump(mode="json"), "client": client.model_dump(mode="json")}
                )

    return EmailDetail(
        email_id=email.id,
        received_at=email.received_at,
        from_name=email.from_name,
        from_email=email.from_email,
        to_address=email.to_address,
        subject=email.subject,
        body=email.body,
        source_text=email.searchable_text,
        prose_source=email.prose_source,
        result=result,
        attorney=attorney,
        matched_clients=matched,
        review_log=repo.list_review_actions(conn, email_id),
    )


@app.post("/api/emails/{email_id}/review", response_model=EmailDetail)
def submit_review(
    email_id: str, request: ReviewRequest, conn: sqlite3.Connection = Depends(get_conn)
) -> EmailDetail:
    """Approve, reject, or correct-and-approve.

    Approving marks the acknowledgment APPROVED_NOT_SENT. There is no route that
    sends it, here or anywhere else.
    """
    result = repo.get_run(conn, email_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no pipeline run for {email_id}")

    action = ReviewAction(
        email_id=email_id,
        outcome=ReviewOutcome(request.outcome),
        reviewer=request.reviewer,
        note=request.note,
        edits=[
            FieldEdit(
                field_path=e.field_path,
                original_value=e.original_value,
                corrected_value=e.corrected_value,
                edited_by=request.reviewer,
                edited_at=REVIEW_CLOCK,
            )
            for e in request.edits
        ],
        acted_at=REVIEW_CLOCK,
    )

    try:
        reviewed = apply_review(result, action)
    except UneditableField as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo.save_run(conn, reviewed, REVIEW_CLOCK)
    repo.record_review(conn, action)
    return email_detail(email_id, conn)


@app.get("/api/attorneys")
def attorneys(conn: sqlite3.Connection = Depends(get_conn)) -> list[dict]:
    return [a.model_dump(mode="json") for a in repo.list_attorneys(conn)]


# --------------------------------------------------------------------------- #
# Static front end
# --------------------------------------------------------------------------- #


def mount_frontend(application: FastAPI, dist: Path) -> bool:
    """Serve the built front end from this process, on the same port as the API.

    Registered after every /api route so the catch-all can never shadow one -- a
    mis-ordered SPA fallback turns a 404 from the API into a 200 of index.html,
    and the front end then tries to parse HTML as JSON. Returns whether anything
    was mounted; in development there is no build and Vite serves it instead.
    """
    if not dist.is_dir():
        return False

    assets = dist / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        candidate = dist / path
        if path and candidate.is_file() and dist in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    return True


mount_frontend(app, FRONTEND_DIST)
