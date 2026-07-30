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


class TestGPTWeeklyUsageParser(unittest.TestCase):
    def test_new_format_rate_limits_list(self):
        from api_client import _parse_gpt_secondary_window
        data = {"data": {"rate_limits": [{"window": "weekly", "used_percent": 42.5, "resets_at": "2026-08-01T00:00:00Z"}]}}
        result = _parse_gpt_secondary_window(data)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["used_percent"], 42.5)
        self.assertAlmostEqual(result["remaining_percent"], 57.5)
        self.assertEqual(result["reset_at"], "2026-08-01T00:00:00Z")

    def test_old_format_secondary_window(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limit": {"secondary_window": {"used_percent": 75.0, "reset_after_seconds": 3600}}}
        result = _parse_gpt_secondary_window(data)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["used_percent"], 75.0)
        self.assertEqual(result["reset_after_seconds"], 3600)

    def test_old_format_weekly_primary_window(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limit": {"secondary_window": None, "primary_window": {
            "used_percent": 30.0,
            "limit_window_seconds": 604800,
            "reset_at": 1785923727,
            "reset_after_seconds": 525327,
        }}}
        result = _parse_gpt_secondary_window(data)
        self.assertIsNotNone(result)
        self.assertEqual(result["used_percent"], 30.0)
        self.assertEqual(result["reset_at"], 1785923727)

    def test_old_format_five_hour_primary_is_not_weekly(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limit": {"secondary_window": None, "primary_window": {
            "used_percent": 30.0,
            "limit_window_seconds": 18000,
        }}}
        self.assertIsNone(_parse_gpt_secondary_window(data))

    def test_string_percent(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limits": [{"window": "weekly", "used_percent": "33.33"}]}
        result = _parse_gpt_secondary_window(data)
        self.assertAlmostEqual(result["used_percent"], 33.33)

    def test_clamp_above_100(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limits": [{"window": "weekly", "used_percent": 150.0}]}
        result = _parse_gpt_secondary_window(data)
        self.assertAlmostEqual(result["used_percent"], 100.0)

    def test_non_finite_percent_returns_none(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limits": [{"window": "weekly", "used_percent": "NaN"}]}
        self.assertIsNone(_parse_gpt_secondary_window(data))

    def test_no_secondary_returns_none(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limits": [{"window": "daily", "used_percent": 10.0}]}
        self.assertIsNone(_parse_gpt_secondary_window(data))

    def test_none_input(self):
        from api_client import _parse_gpt_secondary_window
        self.assertIsNone(_parse_gpt_secondary_window(None))
        self.assertIsNone(_parse_gpt_secondary_window("invalid"))

    def test_rate_limits_object_format(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limits": {"primary": {"used_percent": 10}, "secondary": {"used_percent": 65.5, "resets_at": "2026-08-01T00:00:00Z"}}}
        result = _parse_gpt_secondary_window(data)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["used_percent"], 65.5)
        self.assertEqual(result["reset_at"], "2026-08-01T00:00:00Z")

    def test_rate_limits_object_nested_data(self):
        from api_client import _parse_gpt_secondary_window
        data = {"data": {"rate_limits": {"secondary": {"used_percent": 30.0}}}}
        result = _parse_gpt_secondary_window(data)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["used_percent"], 30.0)

    def test_list_window_7d(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limits": [{"window": "7d", "used_percent": 22.0}]}
        result = _parse_gpt_secondary_window(data)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["used_percent"], 22.0)

    def test_list_window_secondary(self):
        from api_client import _parse_gpt_secondary_window
        data = {"rate_limits": [{"window": "secondary", "used_percent": 44.0}]}
        result = _parse_gpt_secondary_window(data)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["used_percent"], 44.0)

class TestFetchGPTWeeklyUsage(unittest.TestCase):

    def test_session_cookie_uses_get(self):
        from unittest.mock import patch, Mock, call
        from api_client import fetch_gpt_weekly_usage, GPT_AUTH_SESSION_URL, GPT_USAGE_URL
        auth_resp = Mock(status_code=200)
        auth_resp.json.return_value = {"accessToken": "tok", "account": {"id": "aid"}}
        usage_resp = Mock(status_code=200)
        usage_resp.json.return_value = {"rate_limits": {"secondary": {"used_percent": 10.0}}}
        calls = []
        def track_get(url, **kwargs):
            calls.append(url)
            return auth_resp if url == GPT_AUTH_SESSION_URL else usage_resp
        with (patch("api_client._gpt_try_local_auth", return_value=None),
            patch("api_client.requests.get", side_effect=track_get)):
            result = fetch_gpt_weekly_usage(session_cookie="cookie")
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], GPT_AUTH_SESSION_URL)
        self.assertEqual(calls[1], GPT_USAGE_URL)

    def test_session_cookie_two_step(self):
        from unittest.mock import patch, Mock
        from api_client import fetch_gpt_weekly_usage
        auth_resp = Mock(status_code=200)
        auth_resp.json.return_value = {"accessToken": "tok123", "account": {"id": "acc1"}}
        usage_data = {"rate_limits": [{"window": "weekly", "used_percent": 55.0}]}
        usage_resp = Mock(status_code=200)
        usage_resp.json.return_value = usage_data
        with (patch("api_client.requests.get", side_effect=[auth_resp, usage_resp]),
            patch("api_client._gpt_try_local_auth", return_value=None)):
            result = fetch_gpt_weekly_usage(session_cookie="sess_token=abc")
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["data"]["used_percent"], 55.0)
        self.assertEqual(result["data"]["source"], "session_cookie")


    def test_local_auth_account_id_priority(self):
        import pathlib, json, tempfile
        from unittest.mock import patch, Mock
        from api_client import _gpt_try_local_auth
        usage_data = {"rate_limits": {"secondary": {"used_percent": 20.0}}}
        usage_resp = Mock(status_code=200)
        usage_resp.json.return_value = usage_data
        with tempfile.TemporaryDirectory() as td:
            auth_dir = pathlib.Path(td) / ".codex"
            auth_dir.mkdir()
            auth = {"tokens": {"access_token": "tok", "account_id": "from_tokens"}, "account_id": "from_root"}
            (auth_dir / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
            with (
                patch("api_client._pl.Path.home", return_value=pathlib.Path(td)) as _ph,
                patch("api_client.requests.get", return_value=usage_resp) as mock_get,
            ):
                result = _gpt_try_local_auth()
            self.assertIsNotNone(result)
            self.assertEqual(result["source"], "local_codex_auth")
            # Verify tokens.account_id was used (not root.account_id)
            headers = mock_get.call_args[1].get("headers", {})
            self.assertEqual(headers.get("ChatGPT-Account-ID"), "from_tokens")

    def test_no_data_returns_error(self):
        from unittest.mock import patch
        from api_client import fetch_gpt_weekly_usage
        with (patch("api_client._gpt_try_local_auth", return_value=None),
            patch("api_client._gpt_try_session_cookie", return_value=None),
            patch("api_client._gpt_try_jsonl", return_value=None)):
            result = fetch_gpt_weekly_usage()
        self.assertFalse(result["ok"])
        self.assertIn("GPT", result["error"])
        # Must not display 0% for missing data
        self.assertIsNone(result["data"])

    def test_jsonl_fallback(self):
        import json, tempfile, pathlib
        from unittest.mock import patch
        from api_client import fetch_gpt_weekly_usage
        with tempfile.TemporaryDirectory() as td:
            codex_dir = pathlib.Path(td) / ".codex"
            codex_dir.mkdir()
            sessions = codex_dir / "sessions"
            sessions.mkdir()
            jsonl_path = sessions / "rollout-2026.jsonl"
            entry = {"payload": {"rate_limits": [{"window": "weekly", "used_percent": 88.0}]}}
            jsonl_path.write_text(json.dumps(entry), encoding="utf-8")
            with (patch("api_client._gpt_try_local_auth", return_value=None),
                patch("api_client._gpt_try_session_cookie", return_value=None),
                patch("api_client._pl.Path.home", return_value=pathlib.Path(td))):
                result = fetch_gpt_weekly_usage()
            self.assertTrue(result["ok"])
            self.assertAlmostEqual(result["data"]["used_percent"], 88.0)
            self.assertEqual(result["data"]["source"], "local_jsonl")


    def test_jsonl_rate_limits_object(self):
        import json, tempfile, pathlib
        from unittest.mock import patch
        from api_client import fetch_gpt_weekly_usage
        with tempfile.TemporaryDirectory() as td:
            codex_dir = pathlib.Path(td) / ".codex"
            codex_dir.mkdir()
            sessions = codex_dir / "sessions"
            sessions.mkdir()
            jsonl_path = sessions / "rollout-2026.jsonl"
            # rate_limits as dict object (not list)
            entry = {"payload": {"rate_limits": {"secondary": {"used_percent": 77.0}}}}
            jsonl_path.write_text(json.dumps(entry), encoding="utf-8")
            with (patch("api_client._gpt_try_local_auth", return_value=None),
                patch("api_client._gpt_try_session_cookie", return_value=None),
                patch("api_client._pl.Path.home", return_value=pathlib.Path(td))):
                result = fetch_gpt_weekly_usage()
            self.assertTrue(result["ok"])
            self.assertAlmostEqual(result["data"]["used_percent"], 77.0)

    def test_http_error_returns_none(self):
        import pathlib, json, tempfile
        from unittest.mock import patch, Mock
        from api_client import _gpt_try_local_auth
        with tempfile.TemporaryDirectory() as td:
            auth_dir = pathlib.Path(td) / ".codex"
            auth_dir.mkdir()
            (auth_dir / "auth.json").write_text(json.dumps({"tokens": {"access_token": "tok"}}), encoding="utf-8")
            resp = Mock(status_code=403)
            with (patch("api_client._pl.Path.home", return_value=pathlib.Path(td)),
                patch("api_client.requests.get", return_value=resp)):
                result = _gpt_try_local_auth()
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
