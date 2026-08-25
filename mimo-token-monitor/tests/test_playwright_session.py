import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import playwright_session
from playwright_session import PlaywrightSession, PlaywrightSessionError, refresh_cookie


class _FakePage:
    def __init__(self):
        self.goto_calls = []

    def goto(self, *args, **kwargs):
        self.goto_calls.append((args, kwargs))


class _FakeContext:
    def __init__(self):
        self.pages = []
        self.page = _FakePage()
        self.cookies_value = []
        self.close_calls = 0

    def new_page(self):
        self.pages.append(self.page)
        return self.page

    def cookies(self):
        return self.cookies_value

    def close(self):
        self.close_calls += 1


class _FakePlaywright:
    def __init__(self, context):
        self.context = context
        self.launch_calls = []
        self.stop_calls = 0
        self.chromium = self

    def launch_persistent_context(self, *args, **kwargs):
        self.launch_calls.append((args, kwargs))
        return self.context

    def stop(self):
        self.stop_calls += 1


class TestPlaywrightSession(unittest.TestCase):
    def test_start_reuses_isolated_persistent_context_and_opens_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _FakeContext()
            playwright = _FakePlaywright(context)
            manager = Mock()
            manager.start.return_value = playwright
            session = PlaywrightSession(Path(tmp) / "mimo", headless=True)

            with patch.object(playwright_session, "_load_sync_playwright", return_value=lambda: manager):
                self.assertIs(session.start(), context)
                self.assertIs(session.start(), context)
                page = session.open_mimo("https://platform.xiaomimimo.com/")

            self.assertIs(page, context.page)
            self.assertEqual(len(playwright.launch_calls), 1)
            self.assertEqual(playwright.launch_calls[0][0], (str(Path(tmp) / "mimo"),))
            self.assertTrue(playwright.launch_calls[0][1]["headless"])
            self.assertEqual(
                context.page.goto_calls,
                [
                    (
                        ("https://platform.xiaomimimo.com/",),
                        {"wait_until": "domcontentloaded", "timeout": 30_000},
                    )
                ],
            )

    def test_cookie_is_filtered_to_xiaomimimo_without_leaking_other_domains(self):
        context = _FakeContext()
        context.cookies_value = [
            {"name": "mimo_token", "value": "SECRET_COOKIE", "domain": ".xiaomimimo.com"},
            {"name": "session", "value": "SUBDOMAIN_VALUE", "domain": "auth.xiaomimimo.com"},
            {"name": "other", "value": "OTHER_VALUE", "domain": "example.com"},
        ]
        playwright = _FakePlaywright(context)
        manager = Mock()
        manager.start.return_value = playwright
        session = PlaywrightSession(tempfile.mkdtemp())

        with patch.object(playwright_session, "_load_sync_playwright", return_value=lambda: manager):
            self.assertEqual(
                session.get_cookie(),
                "mimo_token=SECRET_COOKIE; session=SUBDOMAIN_VALUE",
            )

        session.close(remove_user_data_dir=True)

    def test_missing_playwright_has_clear_non_sensitive_error(self):
        session = PlaywrightSession(tempfile.mkdtemp())
        with patch.object(
            playwright_session,
            "_load_sync_playwright",
            side_effect=PlaywrightSessionError("未安装 Playwright，请安装依赖"),
        ):
            with self.assertRaisesRegex(PlaywrightSessionError, "未安装 Playwright") as raised:
                session.start()
        self.assertNotIn("SECRET_COOKIE", str(raised.exception))

    def test_existing_chrome_or_edge_profile_is_rejected(self):
        session = PlaywrightSession(
            Path(tempfile.gettempdir()) / "Google" / "Chrome" / "User Data"
        )
        with self.assertRaisesRegex(PlaywrightSessionError, "独立目录"):
            session.start()

    def test_start_failure_redacts_exception_message_and_close_stops_runtime(self):
        context = _FakeContext()
        playwright = _FakePlaywright(context)
        playwright.chromium.launch_persistent_context = Mock(
            side_effect=RuntimeError("cookie=SECRET_COOKIE")
        )
        manager = Mock()
        manager.start.return_value = playwright
        session = PlaywrightSession(tempfile.mkdtemp())

        with patch.object(playwright_session, "_load_sync_playwright", return_value=lambda: manager):
            with self.assertRaises(PlaywrightSessionError) as raised:
                session.start()
        self.assertIn("持久化会话启动失败", str(raised.exception))
        self.assertNotIn("SECRET_COOKIE", str(raised.exception))
        self.assertEqual(playwright.stop_calls, 1)

        session.close()
        self.assertEqual(context.close_calls, 0)

    def test_refresh_cookie_uses_project_profile_and_returns_tuple(self):
        session = Mock()
        session.refresh_cookie.return_value = "mimo_token=SECRET_COOKIE"
        with (
            patch.object(playwright_session, "PlaywrightSession", return_value=session) as session_type,
            patch(
                "config.playwright_user_data_dir",
                return_value="C:/isolated/mimo-profile",
            ),
        ):
            result = refresh_cookie(interactive=True, timeout_seconds=12)

        self.assertEqual(result, ("mimo_token=SECRET_COOKIE", None))
        session_type.assert_called_once_with("C:/isolated/mimo-profile", headless=False)
        session.refresh_cookie.assert_called_once_with(interactive=True, timeout_seconds=12)
        session.close.assert_called_once_with()

    def test_refresh_cookie_returns_clear_error_without_cookie(self):
        session = Mock()
        session.refresh_cookie.side_effect = PlaywrightSessionError(
            "未安装 Playwright，请安装依赖"
        )
        with (
            patch.object(playwright_session, "PlaywrightSession", return_value=session),
            patch("config.playwright_user_data_dir", return_value="C:/isolated/mimo-profile"),
        ):
            cookie, error = refresh_cookie()

        self.assertIsNone(cookie)
        self.assertEqual(error, "未安装 Playwright，请安装依赖")
        self.assertNotIn("SECRET_COOKIE", error)
        session.close.assert_called_once_with()

    def test_interactive_refresh_waits_for_login_cookie(self):
        session = PlaywrightSession(tempfile.mkdtemp())
        session.open_mimo = Mock()
        session.get_cookie = Mock(
            side_effect=[
                PlaywrightSessionError("未找到 xiaomimimo.com Cookie"),
                "session_id=ready",
            ]
        )
        with patch("playwright_session.time.sleep") as sleep:
            with patch("playwright_session.time.monotonic", side_effect=[0, 0, 1]):
                self.assertEqual(
                    session.refresh_cookie(interactive=True, timeout_seconds=10),
                    "session_id=ready",
                )
        sleep.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
