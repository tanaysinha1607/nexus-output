import os
import uuid
import re
from datetime import datetime, timezone
from typing import List, Optional, Literal

from fastapi import Depends, FastAPI, HTTPException, Header, Query, Path, status
from pydantic import BaseModel, EmailStr, Field, validator, ConfigDict

app = FastAPI()


# ---------- In-memory storage ----------
# Each contact is stored as a dict matching the response schema.
_contacts: dict[str, dict] = {}


# ---------- Authentication ----------
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "changeme")  # simple shared secret


def verify_token(authorization: Optional[str] = Header(None)):
    """
    Simple Bearer token authentication.
    Expected header: Authorization: Bearer <TOKEN>
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    try:
        scheme, token = authorization.split()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format",
        )
    if scheme.lower() != "bearer" or token != AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return True


# ---------- Pydantic models ----------
class ContactBase(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

    @validator("phone")
    def phone_pattern(cls, v):
        if v is None:
            return v
        pattern = re.compile(r"^[+]?\\d{7,15}$")
        if not pattern.fullmatch(v):
            raise ValueError("Phone number must match pattern ^[+]?\\d{7,15}$")
        return v


class ContactCreate(ContactBase):
    name: str = Field(..., min_length=1)
    email: EmailStr = ...
    phone: str = ...

    model_config = ConfigDict(extra="forbid")


class ContactUpdate(ContactBase):
    model_config = ConfigDict(extra="forbid")


class ContactResponse(BaseModel):
    id: str
    name: str
    email: EmailStr
    phone: str
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class ContactsListResponse(BaseModel):
    contacts: List[ContactResponse]
    total: int

    model_config = ConfigDict(from_attributes=True)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    timestamp: str

    model_config = ConfigDict(from_attributes=True)


# ---------- Helper functions ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_contact_or_404(contact_id: str) -> dict:
    contact = _contacts.get(contact_id)
    if not contact or contact.get("deleted"):
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


# ---------- Endpoints ----------
@app.post(
    "/api/v1/contacts",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_token)],
)
def create_contact(payload: ContactCreate):
    contact_id = str(uuid.uuid4())
    timestamp = now_iso()
    contact = {
        "id": contact_id,
        "name": payload.name,
        "email": payload.email,
        "phone": payload.phone,
        "created_at": timestamp,
        "updated_at": timestamp,
        "deleted": False,
    }
    _contacts[contact_id] = contact
    return ContactResponse(**contact)


@app.get(
    "/api/v1/contacts",
    response_model=ContactsListResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_token)],
)
def list_contacts(
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
):
    all_contacts = [c for c in _contacts.values() if not c.get("deleted")]
    total = len(all_contacts)
    sliced = all_contacts[offset : offset + limit]
    return ContactsListResponse(
        contacts=[ContactResponse(**c) for c in sliced],
        total=total,
    )


@app.get(
    "/api/v1/contacts/{contact_id}",
    response_model=ContactResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_token)],
)
def get_contact(contact_id: str = Path(..., regex=r"^[0-9a-fA-F-]{36}$")):
    contact = get_contact_or_404(contact_id)
    return ContactResponse(**contact)


@app.put(
    "/api/v1/contacts/{contact_id}",
    response_model=ContactResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_token)],
)
def update_contact(
    payload: ContactUpdate,
    contact_id: str = Path(..., regex=r"^[0-9a-fA-F-]{36}$"),
):
    contact = get_contact_or_404(contact_id)

    # Update mutable fields if provided
    if payload.name is not None:
        contact["name"] = payload.name
    if payload.email is not None:
        contact["email"] = payload.email
    if payload.phone is not None:
        contact["phone"] = payload.phone

    contact["updated_at"] = now_iso()
    _contacts[contact_id] = contact
    return ContactResponse(**contact)


@app.delete(
    "/api/v1/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_token)],
)
def delete_contact(contact_id: str = Path(..., regex=r"^[0-9a-fA-F-]{36}$")):
    contact = get_contact_or_404(contact_id)
    # Soft delete: mark as deleted
    contact["deleted"] = True
    contact["updated_at"] = now_iso()
    _contacts[contact_id] = contact
    return None  # FastAPI will produce an empty response body


@app.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
def health_check():
    return HealthResponse(status="ok", timestamp=now_iso())