from fastapi import FastAPI, HTTPException, Query, Path, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, UUID4
from typing import List, Literal
from datetime import datetime, timezone
from uuid import UUID, uuid4

from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

app = FastAPI()

# In-memory storage for notes
_notes: dict[UUID, dict] = {}

# Prometheus metric
notes_created_counter = Counter(
    "notes_created_total",
    "Total number of notes created"
)

# ---------- Schemas ----------
class NoteCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=5000)

class NoteResponse(BaseModel):
    id: UUID4
    title: str = Field(..., max_length=255)
    content: str = Field(..., max_length=5000)
    createdAt: datetime

class HealthResponse(BaseModel):
    status: Literal["UP"] = Field(..., example="UP")

# ---------- Endpoints ----------
@app.post(
    "/api/v1/notes",
    response_model=NoteResponse,
    status_code=201,
    summary="Create a new note"
)
def create_note(payload: NoteCreateRequest):
    note_id = UUID4(uuid4())
    now = datetime.now(timezone.utc)
    note = {
        "id": note_id,
        "title": payload.title,
        "content": payload.content,
        "createdAt": now,
    }
    _notes[note_id] = note
    notes_created_counter.inc()
    return note

@app.get(
    "/api/v1/notes",
    response_model=List[NoteResponse],
    status_code=200,
    summary="List notes with pagination (sorted by createdAt descending)"
)
def list_notes(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    sorted_notes = sorted(
        _notes.values(),
        key=lambda n: n["createdAt"],
        reverse=True
    )
    return sorted_notes[offset: offset + limit]

@app.get(
    "/api/v1/notes/{id}",
    response_model=NoteResponse,
    status_code=200,
    summary="Retrieve a single note by its UUID"
)
def get_note(id: UUID4 = Path(...)):
    note = _notes.get(id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note

@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=200,
    summary="Liveness probe for CI/CD pipelines"
)
def health():
    return {"status": "UP"}

@app.get(
    "/metrics",
    status_code=200,
    summary="Prometheus metrics endpoint",
    response_class=PlainTextResponse
)
def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)