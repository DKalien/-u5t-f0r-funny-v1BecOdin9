import unittest
from unittest.mock import Mock, patch

from api_client import (
    DEFAULT_THIRD_PARTY_BASE_URL,
    _third_party_usage_url,
    fetch_third_party_usage,
)


class TestThirdPartyUsageUrl(unittest.TestCase):
    def test_default_base_url(self):
        self.assertEqual(
            _third_party_usage_url(""),
            f"{DEFAULT_THIRD_PARTY_BASE_URL}/v1/usage",
        )

    def test_base_url_without_api_path(self):
        self.assertEqual(
            _third_party_usage_url("https://example.test/"),
            "https://example.test/v1/usage",
        )

    def test_base_url_with_v1_path(self):
        self.assertEqual(
            _third_party_usage_url("https://example.test/v1"),
            "https://example.test/v1/usage",
        )

    def test_full_usage_url_is_not_duplicated(self):
        self.assertEqual(
            _third_party_usage_url("https://example.test/v1/usage/"),
            "https://example.test/v1/usage",
        )

    @patch("api_client.requests.get")
    def test_fetch_uses_normalized_v1_path(self, get):
        response = Mock(status_code=401)
        get.return_value = response

        result = fetch_third_party_usage("https://example.test/v1", "test-key")

        self.assertFalse(result["ok"])
        self.assertEqual(result["url"], "https://example.test/v1/usage")
        get.assert_called_once_with(
            "https://example.test/v1/usage",
            headers={"Authorization": "Bearer test-key"},
            timeout=15,
        )


if __name__ == "__main__":
    unittest.main()
