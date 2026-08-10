import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl

app = FastAPI()


# ---------- Authentication ----------
API_KEYS = {key.strip() for key in os.getenv("API_KEYS", "").split(",") if key.strip()}


def get_api_key(x_api_key: Optional[str] = Header(None)):
    if not x_api_key or x_api_key not in API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return x_api_key


# ---------- In-memory storage ----------
# store[api_key][bookmark_id] = BookmarkData
store: Dict[str, Dict[uuid.UUID, "BookmarkData"]] = {}


# ---------- Pydantic models ----------
class BookmarkCreateRequest(BaseModel):
    url: HttpUrl = Field(..., description="The URL to bookmark")
    title: Optional[str] = Field(
        None, max_length=255, description="Optional human-readable title"
    )


class BookmarkResponse(BaseModel):
    id: uuid.UUID
    url: HttpUrl
    title: Optional[str] = None
    created_at: datetime


class ListBookmarksResponse(BaseModel):
    items: List[BookmarkResponse]
    nextPage: Optional[str] = None


class BookmarkData(BaseModel):
    id: uuid.UUID
    url: HttpUrl
    title: Optional[str] = None
    created_at: datetime


# ---------- Helper ----------
def get_user_store(api_key: str) -> Dict[uuid.UUID, BookmarkData]:
    if api_key not in store:
        store[api_key] = {}
    return store[api_key]


# ---------- Endpoints ----------
@app.get("/health", response_model=dict, status_code=200)
def health():
    """Health check required by the generic spec (no auth)."""
    return {"status": "ok"}


@app.get("/healthz", response_model=dict, status_code=200)
def healthz():
    """Health check defined in the contract (no auth)."""
    return {"status": "ok"}


@app.post(
    "/bookmarks",
    response_model=BookmarkResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_api_key)],
)
def create_bookmark(
    payload: BookmarkCreateRequest, api_key: str = Depends(get_api_key)
):
    bookmark_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    bookmark = BookmarkData(
        id=bookmark_id,
        url=payload.url,
        title=payload.title,
        created_at=now,
    )
    user_store = get_user_store(api_key)
    user_store[bookmark_id] = bookmark
    return BookmarkResponse(**bookmark.dict())


@app.get(
    "/bookmarks",
    response_model=ListBookmarksResponse,
    status_code=200,
    dependencies=[Depends(get_api_key)],
)
def list_bookmarks(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    api_key: str = Depends(get_api_key),
):
    user_store = get_user_store(api_key)
    # newest first
    sorted_bookmarks = sorted(
        user_store.values(), key=lambda b: b.created_at, reverse=True
    )
    start = (page - 1) * size
    end = start + size
    slice_ = sorted_bookmarks[start:end]
    items = [BookmarkResponse(**b.dict()) for b in slice_]

    next_page = None
    if end < len(sorted_bookmarks):
        next_page = str(page + 1)  # simple opaque token

    return ListBookmarksResponse(items=items, nextPage=next_page)


@app.get(
    "/bookmarks/{id}",
    response_model=BookmarkResponse,
    status_code=200,
    dependencies=[Depends(get_api_key)],
)
def get_bookmark(id: uuid.UUID, api_key: str = Depends(get_api_key)):
    user_store = get_user_store(api_key)
    bookmark = user_store.get(id)
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    return BookmarkResponse(**bookmark.dict())


@app.delete(
    "/bookmarks/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(get_api_key)],
)
def delete_bookmark(id: uuid.UUID, api_key: str = Depends(get_api_key)):
    user_store = get_user_store(api_key)
    if id not in user_store:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    del user_store[id]
    return None