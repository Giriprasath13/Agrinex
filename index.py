import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
PHONE_COUNTRY_CODE = os.getenv("PHONE_COUNTRY_CODE", "+91").strip()
SUPABASE_TIMEOUT = httpx.Timeout(12.0, connect=5.0)

app = FastAPI(title="AgriNex Authentication API", version="1.0.0")

allowed_origins = [
    item.strip()
    for item in os.getenv(
        "AGRI_FRONTEND_ORIGINS",
        "http://localhost:3000,http://localhost:5500,http://127.0.0.1:5500",
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class RegisterRequest(BaseModel):
    phone: str = Field(min_length=10, max_length=20)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=2, max_length=80)
    role: Literal["customer", "farmer"]
    farm_name: str | None = Field(default=None, max_length=100)


class PhoneRequest(BaseModel):
    phone: str = Field(min_length=10, max_length=20)


class VerifyRequest(PhoneRequest):
    token: str = Field(min_length=6, max_length=6)


class LoginRequest(PhoneRequest):
    password: str = Field(min_length=8, max_length=128)


def _supabase_config() -> tuple[str, str]:
    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    api_key = (
        os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )
    if not base_url or not api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "AgriNex authentication is not configured yet. "
                "Add SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY to the server environment."
            ),
        )
    if not base_url.startswith("https://"):
        raise HTTPException(status_code=503, detail="SUPABASE_URL must use HTTPS.")
    return base_url, api_key


def normalize_phone(raw_phone: str) -> str:
    raw = (raw_phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        normalized = "+" + digits
    elif len(digits) == 10:
        if not re.fullmatch(r"\+\d{1,3}", PHONE_COUNTRY_CODE):
            raise HTTPException(status_code=500, detail="PHONE_COUNTRY_CODE must be in +country-code format.")
        normalized = PHONE_COUNTRY_CODE + digits
    elif 11 <= len(digits) <= 15:
        normalized = "+" + digits
    else:
        raise HTTPException(status_code=422, detail="Enter a valid mobile number.")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", normalized):
        raise HTTPException(status_code=422, detail="Enter a valid international mobile number.")
    return normalized


async def supabase_request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    access_token: str | None = None,
    params: dict | None = None,
):
    base_url, api_key = _supabase_config()
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {access_token or api_key}",
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
            response = await client.request(
                method,
                f"{base_url}{path}",
                headers=headers,
                json=payload,
                params=params,
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Authentication service timed out. Please try again.")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach the authentication service.")

    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}

    if not response.is_success:
        message = (
            data.get("msg")
            or data.get("message")
            or data.get("error_description")
            or data.get("error")
            or "Authentication request failed."
        )
        status = response.status_code if 400 <= response.status_code < 500 else 502
        raise HTTPException(status_code=status, detail=str(message)[:240])
    return data


async def user_profile(access_token: str) -> dict:
    auth_user = await supabase_request(
        "GET", "/auth/v1/user", access_token=access_token
    )
    user_id = auth_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="The access token is invalid.")

    rows = await supabase_request(
        "GET",
        "/rest/v1/agrinex_profiles",
        access_token=access_token,
        params={
            "id": f"eq.{user_id}",
            "select": "id,phone,full_name,role,farm_name,farmer_status",
            "limit": "1",
        },
    )
    if not isinstance(rows, list) or not rows:
        raise HTTPException(
            status_code=503,
            detail="Your account profile is not ready. Confirm the AgriNex database migration is installed.",
        )
    return rows[0]


def session_response(access_token: str, profile: dict) -> dict:
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "name": profile["full_name"],
            "phone": profile["phone"],
            "role": profile["role"],
            "farm_name": profile.get("farm_name"),
            "farmer_status": profile["farmer_status"],
        },
    }


def require_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return token


@app.get("/", include_in_schema=False)
async def home():
    return FileResponse(BASE_DIR / "index.html")


@app.get("/health")
@app.get("/api/health")
async def health():
    configured = bool(
        os.getenv("SUPABASE_URL", "").strip()
        and (
            os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
            or os.getenv("SUPABASE_ANON_KEY", "").strip()
        )
    )
    return {
        "status": "ok",
        "service": "agrinex-auth-api",
        "authentication_configured": configured,
    }


@app.post("/api/auth/register", status_code=202)
async def register(body: RegisterRequest):
    phone = normalize_phone(body.phone)
    if body.role == "farmer" and not (body.farm_name or "").strip():
        raise HTTPException(status_code=422, detail="Enter your farm name.")
    metadata = {
        "full_name": body.name.strip(),
        "role": body.role,
        "farm_name": body.farm_name.strip() if body.farm_name else None,
    }
    result = await supabase_request(
        "POST",
        "/auth/v1/signup",
        payload={"phone": phone, "password": body.password, "data": metadata},
    )

    # A session at signup usually means phone confirmation is disabled.
    # Never hand it to the browser: this flow requires a separately verified SMS OTP.
    if result.get("session") and result["session"].get("access_token"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Phone confirmation is disabled in the Supabase Auth settings. "
                "Enable phone confirmation before allowing AgriNex registrations."
            ),
        )

    return JSONResponse(
        status_code=202,
        content={
            "verification_required": True,
            "phone": phone,
            "message": "A verification code has been requested. Enter the 6-digit code sent by SMS.",
        },
    )


@app.post("/api/auth/resend")
async def resend_code(body: PhoneRequest):
    phone = normalize_phone(body.phone)
    await supabase_request(
        "POST",
        "/auth/v1/resend",
        payload={"phone": phone, "type": "sms"},
    )
    return {"sent": True, "message": "A new verification code has been requested."}


@app.post("/api/auth/verify")
async def verify_code(body: VerifyRequest):
    phone = normalize_phone(body.phone)
    if not body.token.isdigit():
        raise HTTPException(status_code=422, detail="Enter the 6-digit verification code.")
    result = await supabase_request(
        "POST",
        "/auth/v1/verify",
        payload={"phone": phone, "token": body.token, "type": "sms"},
    )
    access_token = result.get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="The verification code could not be confirmed.")
    profile = await user_profile(access_token)
    return session_response(access_token, profile)


@app.post("/api/auth/login")
async def login(body: LoginRequest):
    phone = normalize_phone(body.phone)
    result = await supabase_request(
        "POST",
        "/auth/v1/token?grant_type=password",
        payload={"phone": phone, "password": body.password},
    )
    access_token = result.get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="The mobile number or password is incorrect.")
    profile = await user_profile(access_token)
    return session_response(access_token, profile)


@app.get("/api/auth/me")
async def me(authorization: str | None = Header(default=None)):
    access_token = require_bearer(authorization)
    profile = await user_profile(access_token)
    return {
        "name": profile["full_name"],
        "phone": profile["phone"],
        "role": profile["role"],
        "farm_name": profile.get("farm_name"),
        "farmer_status": profile["farmer_status"],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("index:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
