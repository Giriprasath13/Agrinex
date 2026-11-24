import os
import re
import base64
import binascii
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, quote

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
    extra_headers: dict | None = None,
):
    base_url, api_key = _supabase_config()
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {access_token or api_key}",
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)

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
            "select": "id,phone,full_name,role,farm_name,farmer_status,farm_village,farm_district,farm_pincode,farm_address,preferred_language,farm_type,farm_description,verification_submitted_at,verification_note",
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
            "id": profile["id"],
            "name": profile["full_name"],
            "phone": profile["phone"],
            "role": profile["role"],
            "farm_name": profile.get("farm_name"),
            "farmer_status": profile["farmer_status"],
            "preferred_language": profile.get("preferred_language", "en"),
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
        "id": profile["id"],
        "name": profile["full_name"],
        "phone": profile["phone"],
        "role": profile["role"],
        "farm_name": profile.get("farm_name"),
        "farmer_status": profile["farmer_status"],
        "preferred_language": profile.get("preferred_language", "en"),
        "farm_village": profile.get("farm_village"),
        "farm_district": profile.get("farm_district"),
        "farm_pincode": profile.get("farm_pincode"),
    }



# --- AgriNex farmer-first marketplace API ---
# All marketplace routes use the caller's Supabase access token and database RLS.
# No service-role key is required or accepted by this application.

class ProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=80)
    farm_name: str | None = Field(default=None, max_length=100)
    farm_village: str | None = Field(default=None, max_length=100)
    farm_district: str | None = Field(default=None, max_length=100)
    farm_pincode: str | None = Field(default=None, min_length=6, max_length=6)
    farm_address: str | None = Field(default=None, max_length=300)
    preferred_language: Literal["en", "ta"] | None = None
    farm_type: str | None = Field(default=None, max_length=100)
    farm_description: str | None = Field(default=None, max_length=1000)


class ProductCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=1000)
    category: Literal["Vegetables", "Fruits", "Grains", "Dairy", "Liquids", "Organic", "Other"]
    variety: str | None = Field(default=None, max_length=100)
    grade: str | None = Field(default=None, max_length=50)
    price: float = Field(gt=0, le=10000000)
    market_price: float = Field(gt=0, le=10000000)
    stock: float = Field(ge=0, le=1000000)
    unit: Literal["kg", "g", "unit", "L", "ml", "pack", "bag", "crate", "dozen"] = "kg"
    harvest_date: str | None = None
    available_until: str | None = None
    organic: bool = False
    village: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, min_length=6, max_length=6)
    delivery_minutes: int = Field(default=0, ge=0, le=10080)
    pickup_available: bool = True
    delivery_available: bool = False
    delivery_notes: str | None = Field(default=None, max_length=300)
    image_base64: str | None = None
    image_mime_type: Literal["image/jpeg", "image/png", "image/webp"] | None = None
    image_filename: str | None = Field(default=None, max_length=120)


class CartItemRequest(BaseModel):
    product_id: str = Field(min_length=20, max_length=50)
    quantity: float = Field(gt=0, le=100000)


class AddressCreateRequest(BaseModel):
    label: str = Field(default="Home", max_length=40)
    recipient_name: str | None = Field(default=None, max_length=80)
    mobile: str | None = Field(default=None, max_length=20)
    line1: str = Field(min_length=5, max_length=250)
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    pincode: str = Field(pattern=r"^[0-9]{6}$")
    landmark: str | None = Field(default=None, max_length=120)


class OrderCreateRequest(BaseModel):
    items: list[CartItemRequest] = Field(min_length=1, max_length=40)
    address_id: str | None = None
    address: AddressCreateRequest | None = None
    payment_method: Literal["cash_on_delivery"] = "cash_on_delivery"


class OrderStatusRequest(BaseModel):
    status: Literal[
        "confirmed", "preparing", "ready_for_pickup",
        "picked_up", "out_for_delivery", "delivered", "cancelled"
    ]


class BulkRequestCreate(BaseModel):
    product_name: str = Field(min_length=2, max_length=100)
    category: str = Field(min_length=2, max_length=60)
    quantity: float = Field(gt=0, le=1000000)
    unit: str = Field(default="kg", max_length=20)
    target_price: float | None = Field(default=None, gt=0)
    needed_by: str | None = None
    delivery_pincode: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    delivery_city: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=700)


class BulkOfferCreate(BaseModel):
    product_id: str | None = None
    offered_quantity: float = Field(gt=0, le=1000000)
    unit_price: float = Field(gt=0, le=10000000)
    notes: str | None = Field(default=None, max_length=500)


class BulkOfferDecision(BaseModel):
    status: Literal["accepted", "rejected", "withdrawn"]


class ImageUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=120)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    image_base64: str = Field(min_length=20, max_length=7500000)


def _uuid_or_422(value: str, label: str = "ID") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail=f"Invalid {label}.")


async def _profile_from_header(authorization: str | None, role: str | None = None) -> tuple[str, dict]:
    token = require_bearer(authorization)
    profile = await user_profile(token)
    if role and profile.get("role") != role:
        raise HTTPException(status_code=403, detail=f"This action requires a {role} account.")
    return token, profile


async def _require_approved_farmer(authorization: str | None) -> tuple[str, dict]:
    token, profile = await _profile_from_header(authorization, "farmer")
    if profile.get("farmer_status") != "approved":
        raise HTTPException(
            status_code=403,
            detail="Your farmer account is awaiting verification. You can save drafts, but publishing and accepting orders require approval.",
        )
    return token, profile


def _first_row(data):
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def _safe_product_payload(body: ProductCreateRequest, profile: dict, status: str, image_url: str | None = None) -> dict:
    if body.market_price < body.price:
        raise HTTPException(status_code=422, detail="Market reference price cannot be lower than your selling price.")
    if body.pincode and not re.fullmatch(r"[0-9]{6}", body.pincode):
        raise HTTPException(status_code=422, detail="Enter a valid 6-digit pincode.")
    return {
        "farmer_id": profile["id"],
        "farmer_name": profile["full_name"],
        "farmer_farm_name": profile.get("farm_name") or "Farm",
        "name": body.name.strip(),
        "description": body.description.strip(),
        "category": body.category,
        "variety": body.variety,
        "grade": body.grade,
        "price": body.price,
        "market_price": body.market_price,
        "stock": body.stock,
        "unit": body.unit,
        "harvest_date": body.harvest_date,
        "available_until": body.available_until,
        "organic": body.organic,
        "image_url": image_url,
        "status": status,
        "village": body.village or profile.get("farm_village"),
        "district": body.district or profile.get("farm_district"),
        "pincode": body.pincode or profile.get("farm_pincode"),
        "delivery_minutes": body.delivery_minutes,
        "pickup_available": body.pickup_available,
        "delivery_available": body.delivery_available,
        "delivery_notes": body.delivery_notes,
    }


async def _upload_product_image(token: str, user_id: str, filename: str, mime_type: str, encoded: str) -> str:
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="The product image could not be read.")
    if not content or len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Product images must be smaller than 5 MB.")
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[mime_type]
    object_name = f"{user_id}/{uuid.uuid4().hex}.{extension}"
    base_url, api_key = _supabase_config()
    try:
        async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
            response = await client.post(
                f"{base_url}/storage/v1/object/agrinex-product-images/{quote(object_name, safe='/')}",
                content=content,
                headers={
                    "apikey": api_key,
                    "Authorization": f"Bearer {token}",
                    "Content-Type": mime_type,
                    "x-upsert": "false",
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Image upload timed out. Save the listing as a draft and try again.")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not connect to image storage.")
    if not response.is_success:
        raise HTTPException(status_code=response.status_code if response.status_code < 500 else 502,
                            detail="Image upload failed. Check your connection and try again.")
    return f"{base_url}/storage/v1/object/public/agrinex-product-images/{quote(object_name, safe='/')}"


@app.get("/api/profile")
async def get_profile(authorization: str | None = Header(default=None)):
    _, profile = await _profile_from_header(authorization)
    return profile


@app.patch("/api/profile")
async def update_profile(body: ProfileUpdateRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    changes = body.model_dump(exclude_none=True)
    if not changes:
        return profile
    if "farm_pincode" in changes and not re.fullmatch(r"[0-9]{6}", changes["farm_pincode"] or ""):
        raise HTTPException(status_code=422, detail="Enter a valid 6-digit farm pincode.")
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    rows = await supabase_request(
        "PATCH", "/rest/v1/agrinex_profiles",
        access_token=token,
        params={"id": f"eq.{profile['id']}"},
        payload=changes,
        extra_headers={"Prefer": "return=representation"},
    )
    return _first_row(rows) or await user_profile(token)


@app.post("/api/farmer/verification")
async def submit_farmer_verification(body: ProfileUpdateRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    fields = body.model_dump(exclude_none=True)
    allowed = {"full_name", "farm_name", "farm_village", "farm_district", "farm_pincode",
               "farm_address", "preferred_language", "farm_type", "farm_description"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields.get("farm_name", profile.get("farm_name")):
        raise HTTPException(status_code=422, detail="Enter your farm name.")
    if not fields.get("farm_pincode", profile.get("farm_pincode")) or not re.fullmatch(
        r"[0-9]{6}", fields.get("farm_pincode", profile.get("farm_pincode", "")) or ""
    ):
        raise HTTPException(status_code=422, detail="Enter your farm pincode.")
    fields["verification_submitted_at"] = datetime.now(timezone.utc).isoformat()
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    await supabase_request(
        "PATCH", "/rest/v1/agrinex_profiles",
        access_token=token, params={"id": f"eq.{profile['id']}"},
        payload=fields, extra_headers={"Prefer": "return=representation"},
    )
    return {"submitted": True, "farmer_status": profile["farmer_status"],
            "message": "Your farmer details were saved for administrator review."}


@app.get("/api/products")
async def list_marketplace_products(limit: int = 100, category: str | None = None, search: str | None = None):
    limit = max(1, min(limit, 200))
    params = {
        "select": "id,farmer_id,farmer:farmer_name,farm:farmer_farm_name,name,description,category,variety,grade,price,market_price,stock,unit,harvest_date,available_until,organic,verified:farmer_verified,image_url,status,village,district,pincode,delivery_minutes,pickup_available,delivery_available,delivery_notes,created_at",
        "status": "eq.active",
        "stock": "gt.0",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    if category:
        params["category"] = f"eq.{category}"
    if search:
        clean = search.strip().replace(",", " ").replace("%", "")
        if clean:
            params["name"] = f"ilike.*{clean}*"
    rows = await supabase_request("GET", "/rest/v1/agrinex_products", params=params)
    return rows if isinstance(rows, list) else []


@app.get("/api/farmer/products")
async def list_farmer_products(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    return await supabase_request(
        "GET", "/rest/v1/agrinex_products",
        access_token=token,
        params={
            "select": "id,farmer_id,name,description,category,variety,grade,price,market_price,stock,unit,harvest_date,available_until,organic,verified:farmer_verified,image_url,status,village,district,pincode,delivery_minutes,pickup_available,delivery_available,delivery_notes,created_at",
            "farmer_id": f"eq.{profile['id']}",
            "order": "created_at.desc",
            "limit": "200",
        },
    )


@app.post("/api/products", status_code=201)
async def create_marketplace_product(body: ProductCreateRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    approved = profile.get("farmer_status") == "approved"
    image_url = None
    if body.image_base64:
        image_url = await _upload_product_image(
            token, profile["id"], body.image_filename or body.name,
            body.image_mime_type or "image/jpeg", body.image_base64
        )
    row = _safe_product_payload(body, profile, "active" if approved else "draft", image_url)
    result = await supabase_request(
        "POST", "/rest/v1/agrinex_products",
        access_token=token, payload=row,
        extra_headers={"Prefer": "return=representation"},
    )
    product = _first_row(result)
    if not product:
        raise HTTPException(status_code=502, detail="The listing was not saved. Please retry.")
    return {"product": product, "published": approved,
            "message": "Product published." if approved else "Draft saved. Farmer approval is required before it appears in the marketplace."}


@app.patch("/api/products/{product_id}")
async def update_marketplace_product(product_id: str, body: ProductCreateRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    product_id = _uuid_or_422(product_id, "product ID")
    image_url = None
    if body.image_base64:
        image_url = await _upload_product_image(
            token, profile["id"], body.image_filename or body.name,
            body.image_mime_type or "image/jpeg", body.image_base64
        )
    fields = _safe_product_payload(body, profile, "active" if profile.get("farmer_status") == "approved" else "draft", image_url)
    if image_url is None:
        fields.pop("image_url", None)
    fields.pop("farmer_id", None)
    fields.pop("farmer_name", None)
    fields.pop("farmer_farm_name", None)
    rows = await supabase_request(
        "PATCH", "/rest/v1/agrinex_products",
        access_token=token,
        params={"id": f"eq.{product_id}", "farmer_id": f"eq.{profile['id']}"},
        payload=fields,
        extra_headers={"Prefer": "return=representation"},
    )
    row = _first_row(rows)
    if not row:
        raise HTTPException(status_code=404, detail="Product not found or not owned by this farmer.")
    return row


@app.delete("/api/products/{product_id}")
async def remove_marketplace_product(product_id: str, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    product_id = _uuid_or_422(product_id, "product ID")
    rows = await supabase_request(
        "PATCH", "/rest/v1/agrinex_products",
        access_token=token,
        params={"id": f"eq.{product_id}", "farmer_id": f"eq.{profile['id']}"},
        payload={"status": "archived"},
        extra_headers={"Prefer": "return=representation"},
    )
    if not _first_row(rows):
        raise HTTPException(status_code=404, detail="Product not found or not owned by this farmer.")
    return {"archived": True}


@app.get("/api/cart")
async def get_cart(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_cart_items",
        access_token=token,
        params={
            "select": "id,product_id,quantity,product:agrinex_products(id,farmer_id,farmer:farmer_name,farm:farmer_farm_name,name,description,category,price,market_price,stock,unit,harvest_date,organic,verified:farmer_verified,image_url,status,district,pincode,delivery_minutes)",
            "user_id": f"eq.{profile['id']}",
            "order": "created_at.desc",
        },
    )
    result = []
    for row in rows or []:
        p = row.get("product") or {}
        if not p:
            continue
        p["product_id"] = row["product_id"]
        p["quantity"] = row["quantity"]
        result.append(p)
    return result


@app.post("/api/cart")
async def add_cart_item(body: CartItemRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "customer")
    product_id = _uuid_or_422(body.product_id, "product ID")
    product_rows = await supabase_request(
        "GET", "/rest/v1/agrinex_products",
        access_token=token,
        params={"select": "id,stock,status", "id": f"eq.{product_id}", "limit": "1"},
    )
    product = _first_row(product_rows)
    if not product or product.get("status") != "active":
        raise HTTPException(status_code=404, detail="This product is not currently available.")
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_cart_items",
        access_token=token,
        params={"select": "id,quantity", "user_id": f"eq.{profile['id']}", "product_id": f"eq.{product_id}", "limit": "1"},
    )
    existing = _first_row(rows)
    quantity = float(existing.get("quantity", 0) if existing else 0) + body.quantity
    if quantity > float(product.get("stock") or 0):
        raise HTTPException(status_code=409, detail=f"Only {product.get('stock')} units are currently available.")
    if existing:
        updated = await supabase_request(
            "PATCH", "/rest/v1/agrinex_cart_items",
            access_token=token,
            params={"id": f"eq.{existing['id']}", "user_id": f"eq.{profile['id']}"},
            payload={"quantity": quantity},
            extra_headers={"Prefer": "return=representation"},
        )
        return _first_row(updated) or {"saved": True, "quantity": quantity}
    created = await supabase_request(
        "POST", "/rest/v1/agrinex_cart_items",
        access_token=token,
        payload={"user_id": profile["id"], "product_id": product_id, "quantity": quantity},
        extra_headers={"Prefer": "return=representation"},
    )
    return _first_row(created) or {"saved": True, "quantity": quantity}


@app.patch("/api/cart/{product_id}")
async def set_cart_item_quantity(product_id: str, body: CartItemRequest,
                                 authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "customer")
    product_id = _uuid_or_422(product_id, "product ID")
    if _uuid_or_422(body.product_id, "product ID") != product_id:
        raise HTTPException(status_code=422, detail="Cart product IDs do not match.")
    product_rows = await supabase_request(
        "GET", "/rest/v1/agrinex_products",
        access_token=token,
        params={"select": "id,stock,status", "id": f"eq.{product_id}", "limit": "1"},
    )
    product = _first_row(product_rows)
    if not product or product.get("status") != "active":
        raise HTTPException(status_code=404, detail="This product is no longer available.")
    if body.quantity > float(product.get("stock") or 0):
        raise HTTPException(status_code=409, detail=f"Only {product.get('stock')} units are currently available.")
    rows = await supabase_request(
        "PATCH", "/rest/v1/agrinex_cart_items",
        access_token=token,
        params={"user_id": f"eq.{profile['id']}", "product_id": f"eq.{product_id}"},
        payload={"quantity": body.quantity},
        extra_headers={"Prefer": "return=representation"},
    )
    if not _first_row(rows):
        raise HTTPException(status_code=404, detail="Cart item not found.")
    return _first_row(rows)


@app.delete("/api/cart/{product_id}")
async def remove_cart_item(product_id: str, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    product_id = _uuid_or_422(product_id, "product ID")
    await supabase_request(
        "DELETE", "/rest/v1/agrinex_cart_items",
        access_token=token,
        params={"user_id": f"eq.{profile['id']}", "product_id": f"eq.{product_id}"},
    )
    return {"deleted": True}


@app.delete("/api/cart")
async def clear_cart(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    await supabase_request(
        "DELETE", "/rest/v1/agrinex_cart_items",
        access_token=token, params={"user_id": f"eq.{profile['id']}"},
    )
    return {"cleared": True}


@app.get("/api/addresses")
async def get_addresses(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    return await supabase_request(
        "GET", "/rest/v1/agrinex_addresses",
        access_token=token,
        params={"select": "*", "user_id": f"eq.{profile['id']}", "order": "created_at.desc"},
    )


@app.post("/api/addresses", status_code=201)
async def create_address(body: AddressCreateRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    row = body.model_dump()
    row["user_id"] = profile["id"]
    rows = await supabase_request(
        "POST", "/rest/v1/agrinex_addresses",
        access_token=token, payload=row,
        extra_headers={"Prefer": "return=representation"},
    )
    result = _first_row(rows)
    if not result:
        raise HTTPException(status_code=502, detail="Address could not be saved.")
    return result


@app.get("/api/orders")
async def get_customer_orders(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    if profile.get("role") == "customer":
        owner_filter = {"buyer_id": f"eq.{profile['id']}"}
    elif profile.get("role") == "farmer":
        owner_filter = {"farmer_id": f"eq.{profile['id']}"}
    else:
        raise HTTPException(status_code=403, detail="This account cannot access marketplace orders.")
    return await supabase_request(
        "GET", "/rest/v1/agrinex_orders",
        access_token=token,
        params={
            "select": "id,order_number,buyer_id,farmer_id,status,subtotal,delivery_fee,total,payment_method,payment_status,delivery_address,created_at,items:agrinex_order_items(id,product_id,farmer_id,product_name,unit,quantity,unit_price,market_price,line_total,status)",
            **owner_filter,
            "order": "created_at.desc",
            "limit": "100",
        },
    )


@app.post("/api/orders", status_code=201)
async def create_customer_order(body: OrderCreateRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "customer")
    if body.address_id:
        address_id = _uuid_or_422(body.address_id, "address ID")
        saved = await supabase_request(
            "GET", "/rest/v1/agrinex_addresses",
            access_token=token,
            params={"select": "*", "id": f"eq.{address_id}", "user_id": f"eq.{profile['id']}", "limit": "1"},
        )
        address = _first_row(saved)
        if not address:
            raise HTTPException(status_code=422, detail="Choose a saved delivery address.")
        address_snapshot = {
            "label": address.get("label"),
            "recipient_name": address.get("recipient_name"),
            "mobile": address.get("mobile"),
            "line1": address["line1"],
            "city": address["city"],
            "state": address["state"],
            "pincode": address["pincode"],
            "landmark": address.get("landmark"),
        }
    elif body.address:
        address_snapshot = body.address.model_dump()
        created = await create_address(body.address, authorization)
        address_snapshot["id"] = created["id"]
    else:
        raise HTTPException(status_code=422, detail="Select or add a delivery address.")
    items = [{"product_id": _uuid_or_422(x.product_id, "product ID"), "quantity": x.quantity} for x in body.items]
    if len({x["product_id"] for x in items}) != len(items):
        raise HTTPException(status_code=422, detail="A product can appear only once in the cart.")
    result = await supabase_request(
        "POST", "/rest/v1/rpc/create_agrinex_orders",
        access_token=token,
        payload={"p_items": items, "p_address": address_snapshot, "p_payment_method": body.payment_method},
    )
    cart_cleared = True
    try:
        await supabase_request(
            "DELETE", "/rest/v1/agrinex_cart_items",
            access_token=token,
            params={"user_id": f"eq.{profile['id']}"},
        )
    except HTTPException:
        # The order transaction is already committed. Do not return an error that
        # could encourage the customer to submit the same checkout again.
        cart_cleared = False
    if isinstance(result, dict):
        result["cart_cleared"] = cart_cleared
    return result


@app.post("/api/orders/{order_id}/status")
async def set_order_status(order_id: str, body: OrderStatusRequest, authorization: str | None = Header(default=None)):
    token, _ = await _profile_from_header(authorization)
    order_id = _uuid_or_422(order_id, "order ID")
    return await supabase_request(
        "POST", "/rest/v1/rpc/update_agrinex_order_status",
        access_token=token,
        payload={"p_order_id": order_id, "p_status": body.status},
    )


@app.get("/api/farmer/orders")
async def get_farmer_orders(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    return await supabase_request(
        "GET", "/rest/v1/agrinex_orders",
        access_token=token,
        params={
            "select": "id,order_number,buyer_id,farmer_id,status,subtotal,delivery_fee,total,payment_method,payment_status,delivery_address,created_at,items:agrinex_order_items(id,product_id,farmer_id,product_name,unit,quantity,unit_price,market_price,line_total,status)",
            "farmer_id": f"eq.{profile['id']}",
            "order": "created_at.desc",
            "limit": "100",
        },
    )


@app.get("/api/farmer/earnings")
async def get_farmer_earnings(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    return await supabase_request(
        "GET", "/rest/v1/agrinex_farmer_earnings",
        access_token=token,
        params={"select": "*", "farmer_id": f"eq.{profile['id']}", "order": "earning_date.desc", "limit": "60"},
    )


@app.get("/api/farmer/summary")
async def get_farmer_summary(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    products = await supabase_request(
        "GET", "/rest/v1/agrinex_products", access_token=token,
        params={"select": "id,stock,status", "farmer_id": f"eq.{profile['id']}", "limit": "500"},
    )
    orders = await supabase_request(
        "GET", "/rest/v1/agrinex_orders", access_token=token,
        params={"select": "id,status,total,created_at", "farmer_id": f"eq.{profile['id']}", "limit": "500"},
    )
    delivered = [o for o in orders or [] if o.get("status") == "delivered"]
    pending = [o for o in orders or [] if o.get("status") in ("pending_acceptance", "confirmed", "preparing")]
    return {
        "farmer_status": profile.get("farmer_status"),
        "product_count": len(products or []),
        "active_product_count": sum(1 for p in products or [] if p.get("status") == "active"),
        "low_stock_count": sum(1 for p in products or [] if float(p.get("stock") or 0) <= 20),
        "pending_order_count": len(pending),
        "completed_order_count": len(delivered),
        "completed_order_value": round(sum(float(o.get("total") or 0) for o in delivered), 2),
        "demo_metrics": False,
    }


@app.get("/api/notifications")
async def get_notifications(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    return await supabase_request(
        "GET", "/rest/v1/agrinex_notifications",
        access_token=token,
        params={"select": "*", "user_id": f"eq.{profile['id']}", "order": "created_at.desc", "limit": "100"},
    )


@app.patch("/api/notifications/{notification_id}")
async def mark_notification_read(notification_id: str, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    notification_id = _uuid_or_422(notification_id, "notification ID")
    await supabase_request(
        "PATCH", "/rest/v1/agrinex_notifications",
        access_token=token,
        params={"id": f"eq.{notification_id}", "user_id": f"eq.{profile['id']}"},
        payload={"read": True},
    )
    return {"read": True}


@app.get("/api/bulk-requests")
async def list_bulk_requests(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    return await supabase_request(
        "GET", "/rest/v1/agrinex_bulk_requests",
        access_token=token,
        params={
            "select": "id,buyer_id,buyer_name,product_name,category,quantity,unit,target_price,needed_by,delivery_pincode,delivery_city,notes,status,created_at",
            "order": "created_at.desc",
            "limit": "100",
        },
    )


@app.post("/api/bulk-requests", status_code=201)
async def create_bulk_request(body: BulkRequestCreate, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "customer")
    row = body.model_dump()
    row.update({"buyer_id": profile["id"], "buyer_name": profile["full_name"]})
    result = await supabase_request(
        "POST", "/rest/v1/agrinex_bulk_requests",
        access_token=token, payload=row,
        extra_headers={"Prefer": "return=representation"},
    )
    return _first_row(result) or {"created": True}


@app.post("/api/bulk-requests/{request_id}/offers", status_code=201)
async def make_bulk_offer(request_id: str, body: BulkOfferCreate, authorization: str | None = Header(default=None)):
    token, profile = await _require_approved_farmer(authorization)
    request_id = _uuid_or_422(request_id, "bulk request ID")
    request_rows = await supabase_request(
        "GET", "/rest/v1/agrinex_bulk_requests",
        access_token=token,
        params={"select": "id,status,product_name", "id": f"eq.{request_id}", "limit": "1"},
    )
    if not _first_row(request_rows) or _first_row(request_rows).get("status") not in ("open", "offers_received"):
        raise HTTPException(status_code=404, detail="This bulk request is not available.")
    row = body.model_dump()
    row.update({"request_id": request_id, "farmer_id": profile["id"], "farmer_name": profile["full_name"]})
    if row.get("product_id"):
        row["product_id"] = _uuid_or_422(row["product_id"], "product ID")
        product_rows = await supabase_request(
            "GET", "/rest/v1/agrinex_products",
            access_token=token,
            params={
                "select": "id,farmer_id,stock,status,unit",
                "id": f"eq.{row['product_id']}",
                "farmer_id": f"eq.{profile['id']}",
                "limit": "1",
            },
        )
        product = _first_row(product_rows)
        if not product or product.get("status") != "active":
            raise HTTPException(status_code=422, detail="Choose one of your active product listings for this offer.")
        if body.offered_quantity > float(product.get("stock") or 0):
            raise HTTPException(status_code=409, detail=f"Only {product.get('stock')} {product.get('unit')} are currently available for this offer.")
    result = await supabase_request(
        "POST", "/rest/v1/agrinex_bulk_offers",
        access_token=token, payload=row,
        extra_headers={"Prefer": "return=representation"},
    )
    return _first_row(result) or {"created": True}


@app.get("/api/bulk-requests/{request_id}/offers")
async def list_bulk_offers(request_id: str, authorization: str | None = Header(default=None)):
    token, _ = await _profile_from_header(authorization)
    request_id = _uuid_or_422(request_id, "bulk request ID")
    return await supabase_request(
        "GET", "/rest/v1/agrinex_bulk_offers",
        access_token=token,
        params={"select": "*", "request_id": f"eq.{request_id}", "order": "created_at.desc"},
    )


@app.patch("/api/bulk-offers/{offer_id}")
async def decide_bulk_offer(offer_id: str, body: BulkOfferDecision, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    offer_id = _uuid_or_422(offer_id, "bulk offer ID")
    rows = await supabase_request(
        "PATCH", "/rest/v1/agrinex_bulk_offers",
        access_token=token,
        params={"id": f"eq.{offer_id}"},
        payload={"status": body.status},
        extra_headers={"Prefer": "return=representation"},
    )
    row = _first_row(rows)
    if not row:
        raise HTTPException(status_code=404, detail="Offer not found or you are not allowed to update it.")
    return row


@app.get("/api/admin/farmer-applications")
async def admin_farmer_applications(authorization: str | None = Header(default=None)):
    token, _ = await _profile_from_header(authorization)
    return await supabase_request(
        "POST", "/rest/v1/rpc/admin_list_agrinex_farmer_applications",
        access_token=token, payload={},
    )


@app.post("/api/admin/farmer-applications/{farmer_id}/review")
async def admin_review_farmer(farmer_id: str, decision: Literal["approved", "rejected"],
                              notes: str = "", authorization: str | None = Header(default=None)):
    token, _ = await _profile_from_header(authorization)
    farmer_id = _uuid_or_422(farmer_id, "farmer ID")
    return await supabase_request(
        "POST", "/rest/v1/rpc/admin_review_agrinex_farmer_application",
        access_token=token,
        payload={"p_farmer_id": farmer_id, "p_decision": decision, "p_notes": notes[:500]},
    )


@app.post("/api/products/{product_id}/image")
async def upload_product_image(product_id: str, body: ImageUploadRequest,
                               authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    product_id = _uuid_or_422(product_id, "product ID")
    url = await _upload_product_image(token, profile["id"], body.filename, body.mime_type, body.image_base64)
    rows = await supabase_request(
        "PATCH", "/rest/v1/agrinex_products",
        access_token=token,
        params={"id": f"eq.{product_id}", "farmer_id": f"eq.{profile['id']}"},
        payload={"image_url": url},
        extra_headers={"Prefer": "return=representation"},
    )
    if not _first_row(rows):
        raise HTTPException(status_code=404, detail="Product not found or not owned by this farmer.")
    return {"image_url": url}


@app.get("/api/marketplace/health")
async def marketplace_health():
    return {"status": "ok", "service": "agrinex-marketplace-api",
            "marketplace_configured": bool(os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY"))),
            "online_payments_enabled": False,
            "sms_provider_configured": False}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("index:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
