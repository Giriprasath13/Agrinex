import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import HTTPException

from index import app, _amount_paise, _razorpay_settings, _verify_razorpay_checkout_signature


class MarketplaceApiContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_marketplace_health_is_available_without_supabase_secrets(self):
        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "",
                "SUPABASE_PUBLISHABLE_KEY": "",
                "SUPABASE_ANON_KEY": "",
            },
        ):
            response = self.client.get("/api/marketplace/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertFalse(response.json()["marketplace_configured"])
        self.assertFalse(response.json()["online_payments_enabled"])

    def test_public_catalog_reports_missing_supabase_configuration(self):
        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "",
                "SUPABASE_PUBLISHABLE_KEY": "",
                "SUPABASE_ANON_KEY": "",
            },
        ):
            response = self.client.get("/api/products")

        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json()["detail"].lower())

    def test_private_marketplace_routes_require_a_bearer_token(self):
        protected_gets = (
            "/api/profile",
            "/api/farmer/products",
            "/api/farmer/orders",
            "/api/farmer/earnings",
            "/api/farmer/summary",
            "/api/cart",
            "/api/addresses",
            "/api/orders",
            "/api/notifications",
            "/api/bulk-requests",
            "/api/admin/farmer-applications",
        )
        for path in protected_gets:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)

    def test_farmer_write_routes_require_a_bearer_token(self):
        response = self.client.post("/api/farmer/verification", json={})
        self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/api/products",
            json={
                "name": "Fresh Tomatoes",
                "category": "Vegetables",
                "price": 30,
                "market_price": 40,
                "stock": 10,
                "unit": "kg",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_bulk_request_and_order_creation_require_a_customer_session(self):
        response = self.client.post(
            "/api/bulk-requests",
            json={"product_name": "Tomatoes", "category": "Vegetables", "quantity": 100},
        )
        self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/api/orders",
            json={
                "items": [{"product_id": "00000000-0000-0000-0000-000000000001", "quantity": 1}],
                "address": {
                    "line1": "12 Farm Road",
                    "city": "Coimbatore",
                    "state": "Tamil Nadu",
                    "pincode": "641001",
                },
                "payment_method": "cash_on_delivery",
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_live_razorpay_mode_is_blocked_in_student_pilot(self):
        with patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_live_test_placeholder",
            "RAZORPAY_KEY_SECRET": "not-a-real-secret",
            "RAZORPAY_WEBHOOK_SECRET": "not-a-real-webhook-secret",
            "SUPABASE_SERVICE_ROLE_KEY": "not-a-real-service-key",
            "RAZORPAY_MODE": "live",
        }):
            with self.assertRaises(HTTPException) as caught:
                _razorpay_settings()
        self.assertEqual(caught.exception.status_code, 403)

    def test_razorpay_helpers_use_minor_currency_units(self):
        self.assertEqual(_amount_paise("125.50"), 12550)
        self.assertEqual(_amount_paise("0.01"), 1)

    def test_razorpay_checkout_signature_is_verified_server_side(self):
        import hashlib
        import hmac
        secret = "test-only-secret"
        order_id = "order_test_123"
        payment_id = "pay_test_456"
        expected = hmac.new(
            secret.encode(),
            f"{order_id}|{payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        with patch.dict(os.environ, {
            "RAZORPAY_KEY_ID": "rzp_test_placeholder",
            "RAZORPAY_KEY_SECRET": secret,
            "RAZORPAY_WEBHOOK_SECRET": "test-webhook",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role",
            "RAZORPAY_MODE": "test",
        }):
            self.assertTrue(_verify_razorpay_checkout_signature(order_id, payment_id, expected))
            self.assertFalse(_verify_razorpay_checkout_signature(order_id, payment_id, "0" * 64))

    def test_provider_and_consent_routes_require_authentication(self):
        response = self.client.patch(
            "/api/profile/whatsapp-preferences",
            json={"opted_in": True, "phone": "+919876543210", "language": "en"},
        )
        self.assertEqual(response.status_code, 401)

        response = self.client.post(
            "/api/payments/razorpay/verify",
            json={
                "order_id": "00000000-0000-0000-0000-000000000001",
                "razorpay_order_id": "order_test_123",
                "razorpay_payment_id": "pay_test_123",
                "razorpay_signature": "0" * 64,
            },
        )
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/api/farmer/payout-readiness")
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/api/farmer/price-guidance?commodity=Tomato")
        self.assertEqual(response.status_code, 401)

    def test_marketplace_health_reports_optional_adapters_as_disabled_without_credentials(self):
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "test-publishable",
            "SUPABASE_SERVICE_ROLE_KEY": "",
            "RAZORPAY_KEY_ID": "",
            "RAZORPAY_KEY_SECRET": "",
            "RAZORPAY_WEBHOOK_SECRET": "",
            "WHATSAPP_ACCESS_TOKEN": "",
            "WHATSAPP_PHONE_NUMBER_ID": "",
            "WHATSAPP_GRAPH_API_VERSION": "",
            "WHATSAPP_ORDER_TEMPLATE_NAME": "",
            "SHIPROCKET_API_EMAIL": "",
            "SHIPROCKET_API_PASSWORD": "",
            "SUPABASE_PHONE_SMS_CONFIGURED": "false",
            "DATA_GOV_IN_API_KEY": "",
        }):
            response = self.client.get("/api/marketplace/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["online_payments_enabled"])
        self.assertFalse(body["whatsapp_configured"])
        self.assertFalse(body["shiprocket_configured"])
        self.assertFalse(body["sms_provider_configured"])
        self.assertFalse(body["automatic_payouts_enabled"])

    def test_admin_and_logistics_endpoints_require_authentication(self):
        order_id = "00000000-0000-0000-0000-000000000001"
        routes = (
            ("/api/admin/market-prices/sync?state=Tamil%20Nadu", "GET", None),
            (f"/api/admin/orders/{order_id}/cod-collected", "POST", None),
            (f"/api/orders/{order_id}/shipment", "GET", None),
            (f"/api/orders/{order_id}/shipment", "POST", {
                "length_cm": 20, "breadth_cm": 15, "height_cm": 10,
                "weight_kg": 1, "strategy": "cheapest",
            }),
        )
        for path, method, payload in routes:
            with self.subTest(path=path, method=method):
                response = self.client.request(method, path, json=payload)
                self.assertEqual(response.status_code, 401)

    def test_provider_secrets_are_not_embedded_in_frontend(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", response.text)
        self.assertNotIn("WHATSAPP_ACCESS_TOKEN", response.text)
        self.assertNotIn("RAZORPAY_KEY_SECRET", response.text)

    def test_frontend_contains_farmer_first_marketplace_surfaces(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.text
        for marker in (
            'id="bulkMarketplace"',
            'id="farmerStatusPanel"',
            'id="adminFarmerApplications"',
            "syncFarmerOfflineDrafts",
            "openVoiceSell",
            "openCameraSell",
            "Related fresh picks",
            "productDetailsPageContent",
            "saveWhatsAppPreferences",
            "loadFarmerEvidence",
            "importVerifiedMarketPrices",
            "syncDailyMandiPrices",
            "saveWhatsAppPreferences",
            "Razorpay · Test mode",
            "Farm Data Assistant",
            "Tamil",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
