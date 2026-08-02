import os
import uuid
from datetime import datetime, timedelta
from typing import List, Literal

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, PositiveInt

# ---------------------------------------------------------------------------
# Configuration & Security
# ---------------------------------------------------------------------------
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    raise RuntimeError("Environment variable JWT_SECRET_KEY must be set")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_SECONDS = 15 * 60  # 15 minutes
REFRESH_TOKEN_EXPIRE_SECONDS = 7 * 24 * 60 * 60  # 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# In‑memory storage
# ---------------------------------------------------------------------------
users_by_email: dict[str, dict] = {}
refresh_tokens: set[str] = set()  # simple store for issued refresh tokens


# ---------------------------------------------------------------------------
# Pydantic models (contract‑exact)
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    recaptcha_token: str


class RegisterResponse(BaseModel):
    id: str = Field(..., pattern=r"^[0-9a-fA-F-]{36}$")
    email: EmailStr
    is_active: bool
    is_verified: bool
    created_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: PositiveInt


class PositionItem(BaseModel):
    symbol: str
    quantity: float
    avg_cost_usd: float
    current_price_usd: float
    unrealized_pnl_usd: float


class PortfolioSummaryResponse(BaseModel):
    net_worth_usd: float
    total_pnl_usd: float
    positions: List[PositionItem]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def create_access_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(seconds=ACCESS_TOKEN_EXPIRE_SECONDS)
    to_encode = {"sub": email, "exp": expire}
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(seconds=REFRESH_TOKEN_EXPIRE_SECONDS)
    to_encode = {"sub": email, "exp": expire, "type": "refresh"}
    token = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    refresh_tokens.add(token)
    return token


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    payload = decode_token(credentials.credentials)
    email = payload.get("sub")
    if not email or email not in users_by_email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return users_by_email[email]


# ---------------------------------------------------------------------------
# FastAPI app & routes
# ---------------------------------------------------------------------------
app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/auth/register", response_model=RegisterResponse, status_code=201)
def register(req: RegisterRequest):
    if req.email in users_by_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = str(uuid.uuid4())
    now = datetime.utcnow()
    hashed = pwd_context.hash(req.password)

    user_record = {
        "id": user_id,
        "email": req.email,
        "hashed_password": hashed,
        "is_active": True,
        "is_verified": False,
        "created_at": now,
    }
    users_by_email[req.email] = user_record

    return RegisterResponse(
        id=user_id,
        email=req.email,
        is_active=True,
        is_verified=False,
        created_at=now,
    )


@app.post("/api/v1/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    user = users_by_email.get(req.email)
    if not user or not pwd_context.verify(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token(req.email)
    refresh_token = create_refresh_token(req.email)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_SECONDS,
    )


@app.get("/api/v1/portfolio/summary", response_model=PortfolioSummaryResponse)
def portfolio_summary(current_user: dict = Depends(get_current_user)):
    # Dummy static data – in a real system this would be calculated per user
    example_positions = [
        PositionItem(
            symbol="AAPL",
            quantity=10.0,
            avg_cost_usd=150.0,
            current_price_usd=170.0,
            unrealized_pnl_usd=200.0,
        ),
        PositionItem(
            symbol="GOOGL",
            quantity=5.0,
            avg_cost_usd=2500.0,
            current_price_usd=2600.0,
            unrealized_pnl_usd=500.0,
        ),
    ]

    net_worth = sum(p.quantity * p.current_price_usd for p in example_positions)
    total_pnl = sum(p.unrealized_pnl_usd for p in example_positions)

    return PortfolioSummaryResponse(
        net_worth_usd=net_worth,
        total_pnl_usd=total_pnl,
        positions=example_positions,
    )