import os
import re
import json
import base64
import binascii
import uuid
import hashlib
import hmac
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, quote

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
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
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
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
            "select": "id,phone,full_name,role,farm_name,farmer_status,farm_village,farm_district,farm_pincode,farm_address,preferred_language,farm_type,farm_description,verification_submitted_at,verification_note,whatsapp_phone,whatsapp_opt_in,whatsapp_opt_in_at,whatsapp_language,razorpay_route_account_id,shiprocket_pickup_location",
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





DATA_GOV_MANDI_RESOURCE_ID = os.getenv(
    "DATA_GOV_IN_RESOURCE_ID",
    "9ef84268-d588-465a-a308-a864a43d0070",
).strip()
DATA_GOV_MANDI_SOURCE_URL = (
    "https://data.gov.in/resource/current-daily-price-various-commodities-various-markets-mandi"
)


def _parse_market_date(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_market_amount(value: object) -> float | None:
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except Exception:
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal("100000000"):
        return None
    return float(amount)


async def _fetch_data_gov_mandi_records(
    state: str,
    district: str | None = None,
    commodity: str | None = None,
    max_records: int = 100,
) -> list[dict]:
    api_key = os.getenv("DATA_GOV_IN_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="The data.gov.in API key is not configured. An administrator must add DATA_GOV_IN_API_KEY to the server environment.",
        )
    if not state.strip():
        raise HTTPException(status_code=422, detail="Choose a state to limit the mandi-price query.")
    max_records = max(1, min(int(max_records), 500))
    page_size = min(100, max_records)
    collected: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for offset in range(0, max_records, page_size):
                limit = min(page_size, max_records - offset)
                params = {
                    "api-key": api_key,
                    "format": "json",
                    "limit": str(limit),
                    "offset": str(offset),
                    "filters[state]": state.strip(),
                }
                if district and district.strip():
                    params["filters[district]"] = district.strip()
                if commodity and commodity.strip():
                    params["filters[commodity]"] = commodity.strip()
                response = await client.get(
                    f"https://api.data.gov.in/resource/{quote(DATA_GOV_MANDI_RESOURCE_ID, safe='')}",
                    params=params,
                    headers={"Accept": "application/json"},
                )
                if response.status_code in (401, 403):
                    raise HTTPException(
                        status_code=502,
                        detail="data.gov.in rejected the server API key. Check its validity and quota.",
                    )
                if not response.is_success:
                    raise HTTPException(
                        status_code=502,
                        detail=f"data.gov.in returned HTTP {response.status_code}.",
                    )
                payload = response.json()
                records = payload.get("records") or []
                if not records:
                    break
                for item in records:
                    arrival_date = _parse_market_date(item.get("arrival_date"))
                    min_price = _parse_market_amount(item.get("min_price"))
                    max_price = _parse_market_amount(item.get("max_price"))
                    modal_price = _parse_market_amount(item.get("modal_price"))
                    if not arrival_date or None in (min_price, max_price, modal_price):
                        continue
                    if not (min_price <= modal_price <= max_price):
                        continue
                    record_state = str(item.get("state") or state).strip()
                    record_district = str(item.get("district") or "").strip()
                    market = str(item.get("market") or "").strip()
                    commodity_name = str(item.get("commodity") or "").strip()
                    if not all((record_state, record_district, market, commodity_name)):
                        continue
                    collected.append({
                        "state": record_state,
                        "district": record_district,
                        "market": market,
                        "commodity": commodity_name,
                        "variety": item.get("variety"),
                        "grade": item.get("grade"),
                        "arrival_date": arrival_date,
                        "min_price": min_price,
                        "max_price": max_price,
                        "modal_price": modal_price,
                        "price_unit": "INR_PER_QUINTAL",
                        "source": "AGMARKNET_DATA_GOV_IN",
                        "source_url": DATA_GOV_MANDI_SOURCE_URL,
                    })
                if len(records) < limit:
                    break
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="data.gov.in price feed timed out.")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach the data.gov.in price feed.")
    except ValueError:
        raise HTTPException(status_code=502, detail="data.gov.in returned invalid JSON.")
    return collected


def _service_role_key() -> str:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="This server operation is not enabled. A project administrator must configure the server-only Supabase service-role key.",
        )
    return key


def _razorpay_settings() -> tuple[str, str, str]:
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
    mode = os.getenv("RAZORPAY_MODE", "test").strip().lower()
    if mode not in {"test", "live"}:
        raise HTTPException(status_code=503, detail="RAZORPAY_MODE must be test or live.")
    if not key_id or not key_secret or not webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="Online payments are not configured. The marketplace can continue using Cash on Delivery.",
        )
    if mode == "test" and not key_id.startswith("rzp_test_"):
        raise HTTPException(status_code=503, detail="Test mode requires a Razorpay test key.")
    if mode == "live":
        raise HTTPException(
            status_code=403,
            detail="Live payments are disabled in this student pilot. Only test-mode gateway integration is supported; any future live merchant operation must be owned and supervised by an adult-operated legal entity.",
        )
    _service_role_key()
    return key_id, key_secret, webhook_secret


async def _razorpay_request(method: str, path: str, payload: dict | None = None):
    key_id, key_secret, _ = _razorpay_settings()
    try:
        async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
            response = await client.request(
                method,
                f"https://api.razorpay.com/v1{path}",
                auth=(key_id, key_secret),
                json=payload,
                headers={"Accept": "application/json"},
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Payment provider timed out. No successful payment is assumed.")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach the payment provider.")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not response.is_success:
        description = (
            data.get("error", {}).get("description")
            if isinstance(data.get("error"), dict)
            else None
        )
        raise HTTPException(
            status_code=response.status_code if response.status_code < 500 else 502,
            detail=(description or "The payment provider rejected the request.")[:220],
        )
    return data


def _amount_paise(amount: object) -> int:
    try:
        value = Decimal(str(amount))
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid order amount.")
    if value <= 0 or value > Decimal("10000000"):
        raise HTTPException(status_code=422, detail="Order amount is outside the supported range.")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


async def _fetch_razorpay_payment(payment_id: str) -> dict:
    return await _razorpay_request("GET", f"/payments/{quote(payment_id, safe='')}")


async def _confirm_razorpay_payment_service_role(
    order_id: str, provider_order_id: str, payment_id: str, event_id: str
):
    service_key = _service_role_key()
    return await supabase_request(
        "POST",
        "/rest/v1/rpc/confirm_agrinex_razorpay_payment",
        access_token=service_key,
        payload={
            "p_order_id": order_id,
            "p_provider_order_id": provider_order_id,
            "p_payment_id": payment_id,
            "p_event_id": event_id,
        },
    )


def _verify_razorpay_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    _, key_secret, _ = _razorpay_settings()
    expected = hmac.new(
        key_secret.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _verify_gateway_payment_matches_order(
    payment_id: str, provider_order_id: str, expected_amount_paise: int
) -> dict:
    payment = await _fetch_razorpay_payment(payment_id)
    if payment.get("order_id") != provider_order_id:
        raise HTTPException(status_code=400, detail="Payment belongs to a different gateway order.")
    if payment.get("status") != "captured":
        raise HTTPException(status_code=409, detail="The gateway has not captured this payment yet.")
    if int(payment.get("amount") or 0) != expected_amount_paise or payment.get("currency") != "INR":
        raise HTTPException(status_code=409, detail="The captured amount does not match the AgriNex order.")
    return payment


def _whatsapp_settings() -> tuple[str, str, str, str]:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "").strip()
    template = os.getenv("WHATSAPP_ORDER_TEMPLATE_NAME", "").strip()
    if not (token and phone_id and version and template):
        raise HTTPException(status_code=503, detail="WhatsApp Business messaging is not configured.")
    if not re.fullmatch(r"v\d+\.0", version):
        raise HTTPException(status_code=503, detail="Set a valid WhatsApp Graph API version.")
    return token, phone_id, version, template


async def _send_whatsapp_order_update(
    access_token: str, order_id: str, order_number: str, status_text: str
) -> dict:
    try:
        targets = await supabase_request(
            "POST",
            "/rest/v1/rpc/get_agrinex_whatsapp_targets",
            access_token=_service_role_key(),
            payload={"p_order_id": order_id},
        )
    except HTTPException as exc:
        return {"status": "not_sent", "reason": exc.detail}
    if not targets:
        return {"status": "not_sent", "reason": "No order participant has opted in."}
    try:
        token, phone_id, version, template = _whatsapp_settings()
    except HTTPException as exc:
        return {"status": "not_sent", "reason": exc.detail}
    accepted = 0
    failed = 0
    for target in targets:
        phone = re.sub(r"\D", "", str(target.get("phone") or ""))
        if not phone:
            failed += 1
            continue
        lang = "ta" if target.get("language") == "ta" else "en"
        language_code = os.getenv(
            "WHATSAPP_TEMPLATE_LANGUAGE_TA" if lang == "ta" else "WHATSAPP_TEMPLATE_LANGUAGE_EN",
            "ta" if lang == "ta" else "en",
        ).strip()
        body = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": language_code},
                "components": [{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": order_number[:40]},
                        {"type": "text", "text": status_text[:60]},
                    ],
                }],
            },
        }
        try:
            async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
                response = await client.post(
                    f"https://graph.facebook.com/{version}/{phone_id}/messages",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=body,
                )
            if response.is_success:
                accepted += 1
            else:
                failed += 1
        except httpx.RequestError:
            failed += 1
    return {
        "status": "accepted_by_provider" if accepted else "not_sent",
        "accepted": accepted,
        "failed": failed,
        "delivery_confirmation": "Pending provider delivery webhooks",
    }


def _shiprocket_is_configured() -> bool:
    return all(os.getenv(k, "").strip() for k in (
        "SHIPROCKET_API_EMAIL", "SHIPROCKET_API_PASSWORD"
    ))


async def _shiprocket_token() -> str:
    email = os.getenv("SHIPROCKET_API_EMAIL", "").strip()
    password = os.getenv("SHIPROCKET_API_PASSWORD", "").strip()
    if not email or not password:
        raise HTTPException(status_code=503, detail="Shiprocket API credentials are not configured.")
    try:
        async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
            response = await client.post(
                "https://apiv2.shiprocket.in/v1/external/auth/login",
                json={"email": email, "password": password},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach Shiprocket.")
    data = response.json() if response.content else {}
    if not response.is_success or not data.get("token"):
        raise HTTPException(status_code=502, detail="Shiprocket authentication failed.")
    return data["token"]


async def _shiprocket_request(token: str, method: str, path: str, payload: dict | None = None, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=SUPABASE_TIMEOUT) as client:
            response = await client.request(
                method,
                f"https://apiv2.shiprocket.in/v1/external{path}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                params=params,
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach Shiprocket.")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not response.is_success:
        raise HTTPException(status_code=response.status_code if response.status_code < 500 else 502,
                            detail="Shiprocket did not accept the shipment request.")
    return data



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
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    line1: str = Field(min_length=5, max_length=250)
    city: str = Field(min_length=2, max_length=80)
    state: str = Field(min_length=2, max_length=80)
    pincode: str = Field(pattern=r"^[0-9]{6}$")
    landmark: str | None = Field(default=None, max_length=120)


class OrderCreateRequest(BaseModel):
    items: list[CartItemRequest] = Field(min_length=1, max_length=40)
    address_id: str | None = None
    address: AddressCreateRequest | None = None
    payment_method: Literal["cash_on_delivery", "razorpay"] = "cash_on_delivery"


class RazorpayVerifyRequest(BaseModel):
    order_id: str = Field(min_length=20, max_length=50)
    razorpay_order_id: str = Field(min_length=8, max_length=80)
    razorpay_payment_id: str = Field(min_length=8, max_length=80)
    razorpay_signature: str = Field(min_length=32, max_length=160)


class WhatsAppPreferencesRequest(BaseModel):
    opted_in: bool
    phone: str | None = Field(default=None, min_length=10, max_length=20)
    language: Literal["en", "ta"] = "en"


class ShiprocketBookingRequest(BaseModel):
    length_cm: float = Field(gt=0, le=250)
    breadth_cm: float = Field(gt=0, le=250)
    height_cm: float = Field(gt=0, le=250)
    weight_kg: float = Field(gt=0, le=1000)
    strategy: Literal["cheapest", "fastest"] = "cheapest"


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
        if not p or p.get("status") != "active":
            # Archived/unavailable listings cannot remain purchasable in a cart.
            await supabase_request(
                "DELETE", "/rest/v1/agrinex_cart_items",
                access_token=token,
                params={"id": f"eq.{row['id']}", "user_id": f"eq.{profile['id']}"},
            )
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

    address_snapshot = None
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
            "email": address.get("email"),
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

    items = [
        {"product_id": _uuid_or_422(x.product_id, "product ID"), "quantity": x.quantity}
        for x in body.items
    ]
    if len({x["product_id"] for x in items}) != len(items):
        raise HTTPException(status_code=422, detail="A product can appear only once in the cart.")

    if body.payment_method == "razorpay":
        # The first pilot payment integration is intentionally test-only by default.
        # A live merchant account may only be enabled by an adult-operated legal entity.
        _razorpay_settings()
        product_rows = await supabase_request(
            "GET", "/rest/v1/agrinex_products",
            access_token=token,
            params={
                "select": "id,farmer_id,status,price,stock",
                "id": "in.(" + ",".join(x["product_id"] for x in items) + ")",
                "status": "eq.active",
            },
        )
        if len(product_rows or []) != len(items):
            raise HTTPException(status_code=409, detail="Refresh your cart; one or more products are unavailable.")
        if len({p["farmer_id"] for p in product_rows}) != 1:
            raise HTTPException(
                status_code=422,
                detail="Online checkout currently supports one farmer per payment. Use Cash on Delivery or split the cart by farmer.",
            )

    result = await supabase_request(
        "POST", "/rest/v1/rpc/create_agrinex_orders",
        access_token=token,
        payload={"p_items": items, "p_address": address_snapshot, "p_payment_method": body.payment_method},
    )
    created_orders = result.get("orders", []) if isinstance(result, dict) else []
    if not created_orders:
        raise HTTPException(status_code=502, detail="The order service did not return a saved order.")

    payment = None
    if body.payment_method == "razorpay":
        if len(created_orders) != 1:
            raise HTTPException(status_code=502, detail="Online checkout returned an unexpected number of orders.")
        order = created_orders[0]
        amount_paise = _amount_paise(order.get("total"))
        key_id, _, _ = _razorpay_settings()
        try:
            gateway_order = await _razorpay_request(
                "POST",
                "/orders",
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": str(order["order_number"])[:40],
                    "notes": {"agrinex_order_id": str(order["id"]), "buyer_id": str(profile["id"])},
                },
            )
            provider_order_id = gateway_order.get("id")
            if not provider_order_id or int(gateway_order.get("amount") or 0) != amount_paise:
                raise HTTPException(status_code=502, detail="The payment provider returned an invalid order.")
            await supabase_request(
                "POST", "/rest/v1/rpc/attach_agrinex_payment_order",
                access_token=token,
                payload={"p_order_id": order["id"], "p_provider_order_id": provider_order_id},
            )
        except Exception as exc:
            # The stock reservation is rolled back through the authorized cancellation RPC.
            try:
                await supabase_request(
                    "POST", "/rest/v1/rpc/update_agrinex_order_status",
                    access_token=token,
                    payload={"p_order_id": order["id"], "p_status": "cancelled"},
                )
            except Exception:
                pass
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=502, detail="The payment order could not be initialized.") from exc
        payment = {
            "provider": "razorpay",
            "mode": os.getenv("RAZORPAY_MODE", "test").strip().lower(),
            "key_id": key_id,
            "razorpay_order_id": provider_order_id,
            "amount_paise": amount_paise,
            "currency": "INR",
            "local_order_id": order["id"],
        }
    else:
        # Each farmer receives a separate order record. Notify only opted-in participants.
        for created_order in created_orders:
            await _send_whatsapp_order_update(
                token,
                str(created_order["id"]),
                str(created_order["order_number"]),
                "Order placed - awaiting farmer acceptance",
            )

    cart_cleared = True
    try:
        await supabase_request(
            "DELETE", "/rest/v1/agrinex_cart_items",
            access_token=token,
            params={"user_id": f"eq.{profile['id']}"},
        )
    except HTTPException:
        cart_cleared = False
    return {"orders": created_orders, "payment": payment, "cart_cleared": cart_cleared}

@app.post("/api/orders/{order_id}/status")
async def set_order_status(order_id: str, body: OrderStatusRequest, authorization: str | None = Header(default=None)):
    token, _ = await _profile_from_header(authorization)
    order_id = _uuid_or_422(order_id, "order ID")
    result = await supabase_request(
        "POST", "/rest/v1/rpc/update_agrinex_order_status",
        access_token=token,
        payload={"p_order_id": order_id, "p_status": body.status},
    )
    order_rows = await supabase_request(
        "GET", "/rest/v1/agrinex_orders",
        access_token=token,
        params={"select": "id,order_number", "id": f"eq.{order_id}", "limit": "1"},
    )
    order = _first_row(order_rows)
    if order:
        await _send_whatsapp_order_update(
            token, order_id, str(order["order_number"]),
            "Order status: " + body.status.replace("_", " "),
        )
    return result


@app.get("/api/farmer/orders")
async def get_farmer_orders(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_orders",
        access_token=token,
        params={
            "select": "id,order_number,buyer_id,farmer_id,status,subtotal,delivery_fee,total,payment_method,payment_status,delivery_address,created_at,items:agrinex_order_items(id,product_id,farmer_id,product_name,unit,quantity,unit_price,market_price,line_total,status)",
            "farmer_id": f"eq.{profile['id']}",
            "order": "created_at.desc",
            "limit": "100",
        },
    )
    # Do not expose unpaid Razorpay reservations as fulfilment work.
    return [
        row for row in (rows or [])
        if row.get("payment_method") == "cash_on_delivery"
        or row.get("payment_status") == "captured"
    ]


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
    fulfilment_orders = [
        o for o in (orders or [])
        if o.get("payment_method") == "cash_on_delivery"
        or o.get("payment_status") == "captured"
    ]
    delivered = [o for o in fulfilment_orders if o.get("status") == "delivered"]
    pending = [o for o in fulfilment_orders if o.get("status") in ("pending_acceptance", "confirmed", "preparing")]
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




@app.post("/api/payments/razorpay/verify")
async def verify_razorpay_checkout(body: RazorpayVerifyRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "customer")
    order_id = _uuid_or_422(body.order_id, "order ID")
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_orders",
        access_token=token,
        params={
            "select": "id,buyer_id,order_number,total,payment_method,payment_status,provider_order_id",
            "id": f"eq.{order_id}", "buyer_id": f"eq.{profile['id']}", "limit": "1",
        },
    )
    order = _first_row(rows)
    if not order or order.get("payment_method") != "razorpay":
        raise HTTPException(status_code=404, detail="Online payment order not found.")
    if order.get("provider_order_id") != body.razorpay_order_id:
        raise HTTPException(status_code=400, detail="The gateway order does not match AgriNex.")
    if not _verify_razorpay_checkout_signature(
        body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature
    ):
        raise HTTPException(status_code=400, detail="Payment signature verification failed.")

    expected_amount = _amount_paise(order["total"])
    await _verify_gateway_payment_matches_order(
        body.razorpay_payment_id, body.razorpay_order_id, expected_amount
    )
    result = await _confirm_razorpay_payment_service_role(
        order_id, body.razorpay_order_id, body.razorpay_payment_id,
        f"checkout:{body.razorpay_payment_id}",
    )
    await _send_whatsapp_order_update(
        token, order_id, order["order_number"], "Payment captured - order awaiting farmer acceptance"
    )
    return {"verified": True, "payment": result, "order_number": order["order_number"]}


@app.post("/api/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    _, _, webhook_secret = _razorpay_settings()
    raw_body = await request.body()
    supplied = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid payment webhook signature.")
    event_id = request.headers.get("x-razorpay-event-id", "").strip()
    if not event_id:
        raise HTTPException(status_code=400, detail="Payment event ID is missing.")
    try:
        payload = json.loads(raw_body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON.")
    event_type = payload.get("event")
    if event_type not in {"payment.captured", "order.paid", "payment.failed"}:
        return {"received": True, "processed": False, "reason": "Event is not a supported payment state."}
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {}) or {}
    order_entity = payload.get("payload", {}).get("order", {}).get("entity", {}) or {}
    entity = payment_entity or order_entity
    payment_id = entity.get("id") if entity.get("entity") == "payment" else (
        payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
    )
    provider_order_id = entity.get("order_id") or payload.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id")
    if not payment_id or not provider_order_id:
        raise HTTPException(status_code=400, detail="The captured payment event is missing order details.")

    service_key = _service_role_key()
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_orders",
        access_token=service_key,
        params={
            "select": "id,order_number,total,payment_method,provider_order_id",
            "provider_order_id": f"eq.{provider_order_id}", "limit": "1",
        },
    )
    order = _first_row(rows)
    if not order or order.get("payment_method") != "razorpay":
        raise HTTPException(status_code=404, detail="Matching AgriNex payment order not found.")
    if event_type == "payment.failed":
        service_key = _service_role_key()
        result = await supabase_request(
            "POST", "/rest/v1/rpc/record_agrinex_razorpay_failure",
            access_token=service_key,
            payload={
                "p_order_id": order["id"],
                "p_provider_order_id": provider_order_id,
                "p_payment_id": payment_id,
                "p_event_id": event_id,
                "p_failure_reason": str(payment_entity.get("error_description") or "")[:300],
            },
        )
        # Do not release reserved stock on payment.failed: a later captured event
        # may arrive for the same attempt. Reconcile capture before cancellation.
        await _send_whatsapp_order_update(
            service_key, str(order["id"]), str(order["order_number"]),
            "Payment attempt failed - order not paid",
        )
        return {"received": True, "processed": True, "result": result}

    await _verify_gateway_payment_matches_order(
        payment_id, provider_order_id, _amount_paise(order["total"])
    )
    result = await _confirm_razorpay_payment_service_role(
        order["id"], provider_order_id, payment_id, event_id
    )
    await _send_whatsapp_order_update(
        service_key, str(order["id"]), str(order["order_number"]),
        "Payment captured - order awaiting farmer acceptance",
    )
    return {"received": True, "processed": True, "result": result}


@app.get("/api/orders/{order_id}/shipment")
async def get_order_shipment(order_id: str, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    order_id = _uuid_or_422(order_id, "order ID")
    orders = await supabase_request(
        "GET", "/rest/v1/agrinex_orders",
        access_token=token,
        params={"select": "id,buyer_id,farmer_id", "id": f"eq.{order_id}", "limit": "1"},
    )
    order = _first_row(orders)
    if not order or profile["id"] not in (order["buyer_id"], order["farmer_id"]):
        raise HTTPException(status_code=404, detail="Order not found.")
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_shipments",
        access_token=token,
        params={"select": "*", "order_id": f"eq.{order_id}", "limit": "1"},
    )
    return _first_row(rows) or {"status": "not_booked", "tracking_available": False}


@app.post("/api/orders/{order_id}/shipment")
async def book_order_shipment(
    order_id: str,
    body: ShiprocketBookingRequest,
    authorization: str | None = Header(default=None),
):
    token, farmer = await _require_approved_farmer(authorization)
    order_id = _uuid_or_422(order_id, "order ID")
    if not _shiprocket_is_configured():
        raise HTTPException(status_code=503, detail="Shiprocket API user credentials are not configured.")
    if os.getenv("AGRI_LIVE_LOGISTICS_APPROVED", "").strip().lower() != "true":
        raise HTTPException(
            status_code=403,
            detail="Live carrier booking is disabled for this student pilot. A legally responsible adult-operated business must approve and own any real shipment bookings.",
        )
    pickup_name = (farmer.get("shiprocket_pickup_location") or os.getenv("SHIPROCKET_PICKUP_LOCATION", "")).strip()
    if not pickup_name or not farmer.get("farm_pincode"):
        raise HTTPException(status_code=422, detail="A registered Shiprocket pickup location and farm pincode are required.")
    orders = await supabase_request(
        "GET", "/rest/v1/agrinex_orders",
        access_token=token,
        params={
            "select": "id,order_number,buyer_id,farmer_id,status,subtotal,total,payment_method,payment_status,delivery_address,items:agrinex_order_items(product_name,quantity,unit_price,product_id)",
            "id": f"eq.{order_id}", "farmer_id": f"eq.{farmer['id']}", "limit": "1",
        },
    )
    order = _first_row(orders)
    if not order:
        raise HTTPException(status_code=404, detail="Farmer order not found.")
    if order["status"] not in ("confirmed", "preparing", "ready_for_pickup"):
        raise HTTPException(status_code=409, detail="The farmer must accept the order before booking shipment.")
    if order["payment_method"] == "razorpay" and order["payment_status"] != "captured":
        raise HTTPException(status_code=409, detail="Prepaid orders cannot ship before payment is captured.")
    address = order.get("delivery_address") or {}
    for key in ("recipient_name", "mobile", "email", "line1", "city", "state", "pincode"):
        if not str(address.get(key) or "").strip():
            raise HTTPException(status_code=422, detail=f"Delivery address is missing {key}.")
    shiprocket = await _shiprocket_token()

    serviceability = await _shiprocket_request(
        shiprocket, "GET", "/courier/serviceability/",
        params={
            "pickup_postcode": farmer["farm_pincode"],
            "delivery_postcode": address["pincode"],
            "cod": 1 if order["payment_method"] == "cash_on_delivery" else 0,
            "weight": body.weight_kg,
        },
    )
    available = serviceability.get("data", {}).get("available_courier_companies") or []
    if not available:
        raise HTTPException(status_code=409, detail="No courier currently serves this pickup/delivery pincode and parcel size.")
    if body.strategy == "fastest":
        available.sort(key=lambda x: (
            float(x.get("estimated_delivery_days") or 999),
            float(x.get("freight_charge") or 999999),
        ))
    else:
        available.sort(key=lambda x: (
            float(x.get("freight_charge") or 999999),
            float(x.get("estimated_delivery_days") or 999),
        ))
    courier = available[0]
    order_items = []
    for idx, item in enumerate(order.get("items") or []):
        order_items.append({
            "name": item["product_name"][:100],
            "sku": str(item.get("product_id") or f"AGN-{idx+1}")[:50],
            "units": float(item["quantity"]),
            "selling_price": float(item["unit_price"]),
            "discount": 0,
            "tax": 0,
            "hsn": 0,
        })
    receiver_name = str(address["recipient_name"]).strip().split()
    customer_first = receiver_name[0]
    customer_last = " ".join(receiver_name[1:])[:80] or ""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ship_order = await _shiprocket_request(
        shiprocket,
        "POST",
        "/orders/create/adhoc",
        {
            "order_id": order["order_number"],
            "order_date": now,
            "pickup_location": pickup_name,
            "billing_customer_name": customer_first,
            "billing_last_name": customer_last,
            "billing_address": address["line1"][:190],
            "billing_city": address["city"],
            "billing_pincode": address["pincode"],
            "billing_state": address["state"],
            "billing_country": "India",
            "billing_email": str(address["email"]).strip(),
            "billing_phone": re.sub(r"\D", "", str(address["mobile"])),
            "shipping_is_billing": True,
            "order_items": order_items,
            "payment_method": "COD" if order["payment_method"] == "cash_on_delivery" else "Prepaid",
            "sub_total": float(order["subtotal"]),
            "length": body.length_cm,
            "breadth": body.breadth_cm,
            "height": body.height_cm,
            "weight": body.weight_kg,
        },
    )
    shipment_id = str(ship_order.get("shipment_id") or "")
    if not shipment_id:
        raise HTTPException(status_code=502, detail="Shiprocket did not return a shipment ID.")
    awb = await _shiprocket_request(
        shiprocket,
        "POST",
        "/courier/assign/awb",
        {"shipment_id": int(shipment_id), "courier_id": int(courier["courier_company_id"])},
    )
    awb_data = awb.get("response", {}).get("data", {}) or awb.get("data", {}) or {}
    awb_code = awb_data.get("awb_code")
    if not awb_code:
        raise HTTPException(status_code=502, detail="Shiprocket created the shipment but did not assign an AWB. Check it in the Shiprocket panel.")
    pickup = await _shiprocket_request(
        shiprocket,
        "POST",
        "/courier/generate/pickup",
        {"shipment_id": int(shipment_id)},
    )
    result = await supabase_request(
        "POST", "/rest/v1/rpc/record_agrinex_shipment",
        access_token=token,
        payload={
            "p_order_id": order_id,
            "p_provider": "shiprocket",
            "p_provider_order_id": str(ship_order.get("order_id") or ""),
            "p_shipment_id": shipment_id,
            "p_awb_code": str(awb_code),
            "p_courier_name": str(courier.get("courier_name") or ""),
            "p_tracking_url": str(awb_data.get("tracking_url") or ""),
            "p_pickup_location_name": pickup_name,
            "p_pickup_pincode": farmer["farm_pincode"],
            "p_delivery_pincode": address["pincode"],
            "p_length": body.length_cm,
            "p_breadth": body.breadth_cm,
            "p_height": body.height_cm,
            "p_weight": body.weight_kg,
            "p_status": "pickup_scheduled",
        },
    )
    await _send_whatsapp_order_update(
        token, order_id, order["order_number"], "Shipment booked - pickup scheduled"
    )
    return {
        "shipment": result,
        "courier": courier.get("courier_name"),
        "awb_code": awb_code,
        "pickup_status": pickup.get("pickup_status") or pickup.get("message") or "requested",
        "tracking_url": awb_data.get("tracking_url"),
        "status": "pickup_scheduled",
    }


@app.patch("/api/profile/whatsapp-preferences")
async def update_whatsapp_preferences(body: WhatsAppPreferencesRequest, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization)
    values = {
        "whatsapp_opt_in": body.opted_in,
        "whatsapp_language": body.language,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not body.opted_in:
        values["whatsapp_phone"] = None
    if body.opted_in:
        if not body.phone:
            raise HTTPException(status_code=422, detail="Enter a WhatsApp number before opting in.")
        values["whatsapp_phone"] = normalize_phone(body.phone)
        values["whatsapp_opt_in_at"] = datetime.now(timezone.utc).isoformat()
    rows = await supabase_request(
        "PATCH", "/rest/v1/agrinex_profiles",
        access_token=token,
        params={"id": f"eq.{profile['id']}"},
        payload=values,
        extra_headers={"Prefer": "return=representation"},
    )
    if not _first_row(rows):
        raise HTTPException(status_code=502, detail="WhatsApp preference could not be saved.")
    return {"opted_in": body.opted_in, "language": body.language}


@app.get("/api/farmer/payout-readiness")
async def get_farmer_payout_readiness(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_order_items",
        access_token=token,
        params={
            "select": "id,farmer_payout,payout_status,orders:agrinex_orders(id,order_number,status,payment_method,payment_status)",
            "farmer_id": f"eq.{profile['id']}",
            "status": "eq.delivered",
            "limit": "500",
        },
    )
    eligible = []
    for row in rows or []:
        order = row.get("orders") or {}
        paid = (
            (order.get("payment_method") == "razorpay" and order.get("payment_status") == "captured")
            or (order.get("payment_method") == "cash_on_delivery" and order.get("payment_status") == "collected")
        )
        if paid and row.get("payout_status") == "not_settled":
            eligible.append(row)
    return {
        "eligible_count": len(eligible),
        "eligible_amount_inr": round(sum(float(x.get("farmer_payout") or 0) for x in eligible), 2),
        "automatic_transfer_enabled": False,
        "status": "eligible_for_manual_review" if eligible else "no_reconciled_payouts",
        "message": "An adult-operated marketplace account must complete provider onboarding and approve settlements. This endpoint does not move money.",
    }


@app.get("/api/farmer/price-guidance")
async def get_farmer_price_guidance(commodity: str, district: str | None = None, authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    name = commodity.strip()[:100]
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Enter a product name.")
    district_name = (district or profile.get("farm_district") or "").strip()
    params = {
        "select": "state,district,market,commodity,variety,grade,arrival_date,min_price,max_price,modal_price,price_unit,source,source_url,imported_at",
        "commodity": f"ilike.*{name.replace(',', ' ').replace('%', '')}*",
        "order": "arrival_date.desc",
        "limit": "30",
    }
    if district_name:
        params["district"] = f"ilike.*{district_name.replace(',', ' ').replace('%', '')}*"
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_market_price_observations",
        access_token=token,
        params=params,
    )
    if not rows:
        return {
            "available": False,
            "commodity": name,
            "district": district_name or None,
            "message": "No verified mandi-price observations have been imported for this commodity and district. No price estimate is generated.",
            "data_source_required": "AGMARKNET / data.gov.in, with source URL and arrival date",
        }
    observations = []
    for row in rows:
        unit = row.get("price_unit") or "INR_PER_QUINTAL"
        scale = 100 if unit == "INR_PER_QUINTAL" else 1
        observations.append({
            **row,
            "min_price_per_kg": round(float(row["min_price"]) / scale, 2),
            "max_price_per_kg": round(float(row["max_price"]) / scale, 2),
            "modal_price_per_kg": round(float(row["modal_price"]) / scale, 2),
        })
    return {
        "available": True,
        "commodity": name,
        "district": district_name or None,
        "unit": "INR_PER_KG",
        "observations": observations,
        "suggestion": "Compare these dated wholesale market observations with your grade, farm-gate costs, handling and transport before setting a retail price. Mandi modal price is not a guaranteed farm-gate selling price.",
    }


@app.get("/api/farmer/demand-signals")
async def get_farmer_demand_signals(authorization: str | None = Header(default=None)):
    token, profile = await _profile_from_header(authorization, "farmer")
    rows = await supabase_request(
        "GET", "/rest/v1/agrinex_order_items",
        access_token=token,
        params={
            "select": "product_name,quantity,unit,status,created_at",
            "farmer_id": f"eq.{profile['id']}",
            "order": "created_at.desc",
            "limit": "500",
        },
    )
    totals = {}
    for row in rows or []:
        key = (row["product_name"], row["unit"])
        item = totals.setdefault(key, {"product_name": row["product_name"], "unit": row["unit"], "ordered_quantity": 0.0, "delivered_quantity": 0.0, "cancelled_quantity": 0.0})
        item["ordered_quantity"] += float(row["quantity"] or 0)
        if row["status"] == "delivered":
            item["delivered_quantity"] += float(row["quantity"] or 0)
        if row["status"] == "cancelled":
            item["cancelled_quantity"] += float(row["quantity"] or 0)
    return {
        "source": "AgriNex own completed and recorded order items",
        "period": "All retained order history (maximum 500 most recent order items)",
        "signals": sorted(totals.values(), key=lambda x: x["delivered_quantity"], reverse=True),
        "is_external_market_demand": False,
    }



@app.post("/api/admin/market-prices/sync")
async def sync_market_prices_from_data_gov(
    state: str,
    district: str | None = None,
    commodity: str | None = None,
    limit: int = 100,
    authorization: str | None = Header(default=None),
):
    token, _ = await _profile_from_header(authorization)
    rows = await _fetch_data_gov_mandi_records(
        state=state,
        district=district,
        commodity=commodity,
        max_records=limit,
    )
    if not rows:
        return {
            "fetched": 0,
            "imported": 0,
            "state": state,
            "message": "The source returned no valid rows for these filters. No market price was generated.",
            "source": DATA_GOV_MANDI_SOURCE_URL,
        }
    result = await supabase_request(
        "POST", "/rest/v1/rpc/admin_import_agrinex_market_prices",
        access_token=token,
        payload={"p_rows": rows},
    )
    imported = int((result or {}).get("upserted", 0)) if isinstance(result, dict) else 0
    return {
        "fetched": len(rows),
        "imported": imported,
        "state": state,
        "source": DATA_GOV_MANDI_SOURCE_URL,
        "resource_id": DATA_GOV_MANDI_RESOURCE_ID,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "price_unit": "INR_PER_QUINTAL",
        "message": "Daily wholesale observations were imported. They are not guaranteed farm-gate prices.",
    }


@app.post("/api/admin/market-prices/import")
async def import_market_prices(rows: list[dict], authorization: str | None = Header(default=None)):
    token, _ = await _profile_from_header(authorization)
    result = await supabase_request(
        "POST", "/rest/v1/rpc/admin_import_agrinex_market_prices",
        access_token=token,
        payload={"p_rows": rows[:500]},
    )
    return result



@app.post("/api/admin/orders/{order_id}/cod-collected")
async def admin_confirm_cod_collection(order_id: str, authorization: str | None = Header(default=None)):
    token, _ = await _profile_from_header(authorization)
    order_id = _uuid_or_422(order_id, "order ID")
    return await supabase_request(
        "POST", "/rest/v1/rpc/admin_confirm_agrinex_cod_collection",
        access_token=token,
        payload={"p_order_id": order_id},
    )


@app.get("/api/marketplace/health")
async def marketplace_health():
    razorpay_ready = all(os.getenv(k, "").strip() for k in (
        "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "SUPABASE_SERVICE_ROLE_KEY"
    ))
    razorpay_mode = os.getenv("RAZORPAY_MODE", "test").strip().lower()
    live_approved = os.getenv("AGRI_LIVE_COMMERCE_APPROVED", "").strip().lower() == "true"
    razorpay_ready = razorpay_ready and razorpay_mode == "test"
    whatsapp_ready = all(os.getenv(k, "").strip() for k in (
        "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_GRAPH_API_VERSION", "WHATSAPP_ORDER_TEMPLATE_NAME"
    ))
    shiprocket_ready = _shiprocket_is_configured()
    sms_ready = os.getenv("SUPABASE_PHONE_SMS_CONFIGURED", "").strip().lower() == "true"
    return {
        "status": "ok",
        "service": "agrinex-marketplace-api",
        "marketplace_configured": bool(os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY"))),
        "integrations": {
            "sms": {"configured": sms_ready, "note": "Verified manually in Supabase Auth dashboard; no provider secret is exposed here."},
            "razorpay": {"configured": razorpay_ready, "mode": razorpay_mode if razorpay_ready else "disabled", "live_commerce_approved": live_approved},
            "whatsapp": {"configured": whatsapp_ready, "requires_opt_in": True, "uses_approved_template": True},
            "shiprocket": {
                "configured": shiprocket_ready,
                "booking_allowed": shiprocket_ready and os.getenv("AGRI_LIVE_LOGISTICS_APPROVED", "").strip().lower() == "true",
                "booking_requires_farmer_acceptance": True,
                "live_booking_disabled_for_student_pilot": os.getenv("AGRI_LIVE_LOGISTICS_APPROVED", "").strip().lower() != "true",
            },
            "market_prices": {
                "import_enabled": True,
                "daily_api_configured": bool(os.getenv("DATA_GOV_IN_API_KEY", "").strip()),
                "resource_id": DATA_GOV_MANDI_RESOURCE_ID,
                "verified_observations_only": True,
                "frequency": "daily dataset; not real-time",
            },
            "automatic_farmer_transfers": {"enabled": False, "reason": "Adult-operated marketplace and seller settlement onboarding is required."}
        },
        "online_payments_enabled": razorpay_ready,
        "sms_provider_configured": sms_ready,
        "whatsapp_configured": whatsapp_ready,
        "shiprocket_configured": shiprocket_ready,
        "automatic_payouts_enabled": False,
    }

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("index:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
