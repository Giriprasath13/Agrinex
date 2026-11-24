import unittest

from fastapi import HTTPException

from index import normalize_phone


class PhoneNormalizationTests(unittest.TestCase):
    def test_indian_ten_digit_number_uses_country_code(self):
        self.assertEqual(normalize_phone("9876543210"), "+919876543210")

    def test_e164_input_is_preserved(self):
        self.assertEqual(normalize_phone("+919876543210"), "+919876543210")

    def test_indian_number_with_spaces_and_dashes(self):
        self.assertEqual(normalize_phone("98765 43210"), "+919876543210")

    def test_invalid_short_number_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            normalize_phone("12345")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_invalid_country_code_shape_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            normalize_phone("+000000000000")
        self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
