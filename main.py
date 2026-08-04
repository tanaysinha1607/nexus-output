import os
import uuid
import random
import string
from datetime import datetime, timezone, date
from typing import Optional, List, Dict

from fastapi import FastAPI, Header, HTTPException, Depends, status
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, Field, HttpUrl, validator

app = FastAPI()


# ---------- In‑memory storage ----------
api_keys_by_id: Dict[str, Dict] = {}
api_keys_by_key: Dict[str, Dict] = {}

links_by_id: Dict[str, Dict] = {}
links_by_alias: Dict[str, Dict] = {}

# ---------- Helper utilities ----------
def generate_api_key(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=length))


def generate_alias(length: int = 6) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=length))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Authentication ----------
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")  # required for admin actions


def require_admin(x_api_key: str = Header(..., alias="X-API-Key")):
    if ADMIN_API_KEY is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfiguration: ADMIN_API_KEY not set",
        )
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin API key")
    return x_api_key


def get_caller_key(
    x_api_key: str = Header(..., alias="X-API-Key")
) -> Dict:
    key_record = api_keys_by_key.get(x_api_key)
    if not key_record:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return key_record


# ---------- Pydantic models ----------
class CreateKeyRequest(BaseModel):
    owner: Optional[str] = None
    quota: Optional[int] = Field(
        default=None, ge=1, description="Optional daily request quota for the key."
    )


class CreateKeyResponse(BaseModel):
    id: str = Field(..., description="UUID of the key")
    key: str = Field(..., description="Plain API key (only shown once).")
    owner: Optional[str] = None
    quota: Optional[int] = None
    created_at: str = Field(..., description="ISO‑8601 timestamp")


class CreateLinkRequest(BaseModel):
    target_url: HttpUrl = Field(..., description="Destination URL.")
    alias: Optional[str] = Field(
        default=None,
        pattern="^[A-Za-z0-9]{6,}$",
        description="Optional custom alias.",
    )
    expires_at: Optional[datetime] = Field(
        default=None, description="Optional expiration timestamp."
    )

    @validator("expires_at", pre=True)
    def parse_expires(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v


class CreateLinkResponse(BaseModel):
    id: str
    alias: str
    target_url: HttpUrl
    owner_key_id: str
    created_at: str
    expires_at: Optional[str] = None
    click_count: int = 0


class StatsResponse(BaseModel):
    total_clicks: int
    daily: List[Dict] = Field(default_factory=list)
    user_agents: List[Dict] = Field(default_factory=list)
    referrers: List[Dict] = Field(default_factory=list)


# ---------- Endpoints ----------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/keys", response_model=CreateKeyResponse, status_code=201)
def create_key(
    payload: CreateKeyRequest,
    _: str = Depends(require_admin),
):
    key_id = str(uuid.uuid4())
    plain_key = generate_api_key()
    record = {
        "id": key_id,
        "key": plain_key,
        "owner": payload.owner,
        "quota": payload.quota,
        "created_at": now_iso(),
    }
    api_keys_by_id[key_id] = record
    api_keys_by_key[plain_key] = record
    return CreateKeyResponse(
        id=key_id,
        key=plain_key,
        owner=payload.owner,
        quota=payload.quota,
        created_at=record["created_at"],
    )


@app.delete("/keys/{id}", status_code=204)
def delete_key(
    id: str,
    _: str = Depends(require_admin),
):
    key_record = api_keys_by_id.pop(id, None)
    if not key_record:
        raise HTTPException(status_code=404, detail="Key not found")
    api_keys_by_key.pop(key_record["key"], None)
    return JSONResponse(status_code=204, content=None)


@app.post("/links", response_model=CreateLinkResponse, status_code=201)
def create_link(
    payload: CreateLinkRequest,
    caller: Dict = Depends(get_caller_key),
):
    # Resolve alias
    alias = payload.alias
    if alias:
        if alias in links_by_alias:
            raise HTTPException(status_code=400, detail="Alias already in use")
    else:
        # generate unique alias
        for _ in range(10):
            alias = generate_alias()
            if alias not in links_by_alias:
                break
        else:
            raise HTTPException(status_code=500, detail="Failed to generate unique alias")

    link_id = str(uuid.uuid4())
    created_at = now_iso()
    expires_at_iso = payload.expires_at.isoformat() if payload.expires_at else None

    link_record = {
        "id": link_id,
        "alias": alias,
        "target_url": str(payload.target_url),
        "owner_key_id": caller["id"],
        "created_at": created_at,
        "expires_at": expires_at_iso,
        "click_count": 0,
        # simple stats placeholders
        "daily": [],
        "user_agents": [],
        "referrers": [],
    }

    links_by_id[link_id] = link_record
    links_by_alias[alias] = link_record

    return CreateLinkResponse(
        id=link_id,
        alias=alias,
        target_url=payload.target_url,
        owner_key_id=caller["id"],
        created_at=created_at,
        expires_at=expires_at_iso,
        click_count=0,
    )


@app.get("/links/{id}/stats", response_model=StatsResponse)
def get_link_stats(
    id: str,
    _: Dict = Depends(get_caller_key),
):
    link = links_by_id.get(id)
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    return StatsResponse(
        total_clicks=link["click_count"],
        daily=link.get("daily", []),
        user_agents=link.get("user_agents", []),
        referrers=link.get("referrers", []),
    )


@app.get("/{alias}", status_code=301)
def redirect_alias(alias: str):
    link = links_by_alias.get(alias)
    if not link:
        raise HTTPException(status_code=404, detail="Alias not found")
    # Optional expiration handling
    if link["expires_at"]:
        expires = datetime.fromisoformat(link["expires_at"])
        if expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Link has expired")
    # Increment click count
    link["click_count"] += 1
    return RedirectResponse(url=link["target_url"], status_code=301)