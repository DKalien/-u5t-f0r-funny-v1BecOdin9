import unittest
from unittest.mock import Mock, patch

from api_client import (
    DEFAULT_THIRD_PARTY_BASE_URL,
    _third_party_usage_url,
    fetch_third_party_usage,
    parse_third_party_usage,
)


class TestThirdPartyUsageUrl(unittest.TestCase):
    def test_default_base_url(self):
        self.assertEqual(DEFAULT_THIRD_PARTY_BASE_URL, "https://codex.wlbclub.com")
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


class TestThirdPartyUsageParser(unittest.TestCase):
    def test_selected_window_preserves_reset_at(self):
        data = {
            "rate_limits": [
                {"window": "1d", "used": 1, "limit": 10, "reset_at": "ignored"},
                {
                    "window": "7d",
                    "used": 25,
                    "limit": 100,
                    "reset_at": "2026-09-01T00:00:00Z",
                },
            ]
        }

        result = parse_third_party_usage(data)

        self.assertEqual(result["reset_at"], "2026-09-01T00:00:00Z")
        self.assertEqual(result["used_percent"], 25.0)

    def test_missing_reset_at_is_backward_compatible(self):
        result = parse_third_party_usage(
            {"rate_limits": [{"window": "7d", "used": 25, "limit": 100}]}
        )

        self.assertIsNone(result["reset_at"])

    @patch("api_client.requests.get")
    def test_fetch_keeps_weekly_flat_and_attaches_daily(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "rate_limits": [
                {"window": "1d", "used": 10, "limit": 50},
                {"window": "7d", "used": 25, "limit": 100},
            ]
        }
        get.return_value = response

        result = fetch_third_party_usage("https://example.test", "test-key")

        self.assertTrue(result["ok"])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(result["data"]["window"], "7d")
        self.assertEqual(result["data"]["used_percent"], 25.0)
        self.assertEqual(result["data"]["daily"]["window"], "1d")
        self.assertEqual(result["data"]["daily"]["used_percent"], 20.0)

    @patch("api_client.requests.get")
    def test_missing_daily_window_does_not_fail_weekly_fetch(self, get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "rate_limits": [{"window": "7d", "used": 25, "limit": 100}]
        }
        get.return_value = response

        result = fetch_third_party_usage("https://example.test", "test-key")

        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["daily"]["has_rate_limit"])


class TestGPTWeeklyUsageParser(unittest.TestCase):
    def test_new_format_extracts_primary_and_secondary_windows(self):
        from api_client import _parse_gpt_windows

        data = {
            "rate_limits": {
                "primary": {"used_percent": 12.5, "reset_after_seconds": 3600},
                "secondary": {"used_percent": 42.5, "reset_after_seconds": 86400},
            }
        }
        result = _parse_gpt_windows(data)
        self.assertAlmostEqual(result["primary"]["used_percent"], 12.5)
        self.assertAlmostEqual(result["secondary"]["used_percent"], 42.5)

    def test_list_windows_identified_by_duration(self):
        from api_client import _parse_gpt_windows

        data = {"rate_limits": [
            {"limit_window_seconds": 18000, "used_percent": 10},
            {"limit_window_seconds": 604800, "used_percent": 60},
        ]}
        result = _parse_gpt_windows(data)
        self.assertEqual(result["primary"]["used_percent"], 10.0)
        self.assertEqual(result["secondary"]["used_percent"], 60.0)

    def test_only_primary_window_has_no_weekly_result(self):
        from api_client import _parse_gpt_secondary_window

        data = {"rate_limits": {"primary": {"used_percent": 10}}}
        self.assertIsNone(_parse_gpt_secondary_window(data))

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

    def test_transient_status_is_retried_once(self):
        from unittest.mock import Mock, patch
        from api_client import _gpt_get
        unavailable = Mock(status_code=503)
        success = Mock(status_code=200)
        with patch("api_client.requests.get", side_effect=[unavailable, success]) as get:
            result = _gpt_get("https://example.test", {})
        self.assertIs(result, success)
        self.assertEqual(get.call_count, 2)

    def test_session_cookie_uses_get(self):
        from unittest.mock import patch, Mock
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
        import json
        import pathlib
        import tempfile
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

    def test_failure_reports_each_source_reason(self):
        import pathlib
        import tempfile
        from unittest.mock import patch
        from api_client import fetch_gpt_weekly_usage
        with tempfile.TemporaryDirectory() as td:
            with patch("api_client._pl.Path.home", return_value=pathlib.Path(td)):
                result = fetch_gpt_weekly_usage()
        self.assertFalse(result["ok"])
        self.assertIn("本机 Codex 登录: 未找到登录令牌", result["error"])
        self.assertIn("ChatGPT Cookie: 未配置", result["error"])
        self.assertIn("本地会话: 会话目录不存在", result["error"])

    def test_jsonl_fallback(self):
        import json
        import pathlib
        import tempfile
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
        import json
        import pathlib
        import tempfile
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
        import json
        import pathlib
        import tempfile
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
