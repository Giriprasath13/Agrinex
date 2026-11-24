import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from index import app


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
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
