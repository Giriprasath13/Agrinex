import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from index import app


class AuthApiContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_endpoint_is_available_without_supabase_secrets(self):
        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "",
                "SUPABASE_PUBLISHABLE_KEY": "",
                "SUPABASE_ANON_KEY": "",
            },
        ):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertFalse(response.json()["authentication_configured"])

    def test_me_requires_bearer_token(self):
        response = self.client.get("/api/auth/me")

        self.assertEqual(response.status_code, 401)
        self.assertIn("sign in", response.json()["detail"].lower())

    def test_customer_and_farmer_signup_forms_include_hidden_sms_otp_panels(self):
        html_path = Path(__file__).resolve().parents[1] / "index.html"
        html = html_path.read_text(encoding="utf-8")

        for role in ("customer", "farmer"):
            panel_id = f'id="{role}RegisterOtp"'
            self.assertIn(panel_id, html)
            self.assertIn('pattern="[0-9]{6}"', html)
            self.assertIn(panel_id + " hidden", html)

    def test_register_returns_clear_configuration_error_without_supabase(self):
        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "",
                "SUPABASE_PUBLISHABLE_KEY": "",
                "SUPABASE_ANON_KEY": "",
            },
        ):
            response = self.client.post(
                "/api/auth/register",
                json={
                    "phone": "9876543210",
                    "password": "example-password-123",
                    "name": "Test Customer",
                    "role": "customer",
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
