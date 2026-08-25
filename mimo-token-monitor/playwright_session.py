"""MiMo 平台的可选 Playwright 持久化会话。"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


MIMO_URL = "https://platform.xiaomimimo.com/"
TARGET_URL = MIMO_URL
COOKIE_DOMAIN = "xiaomimimo.com"
DEFAULT_USER_DATA_DIR = Path(tempfile.gettempdir()) / "mimo-token-monitor-playwright"


class PlaywrightSessionError(RuntimeError):
    """Playwright 未安装、启动、导航或关闭失败。"""


def _load_sync_playwright() -> Callable[[], Any]:
    """懒加载可选依赖，避免普通启动路径要求安装 Playwright。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PlaywrightSessionError(
            "未安装 Playwright，请执行：python -m pip install playwright "
            "&& python -m playwright install chromium"
        ) from exc
    return sync_playwright


def _is_mimo_domain(domain: object) -> bool:
    value = str(domain or "").lstrip(".").rstrip(".").lower()
    return value == COOKIE_DOMAIN or value.endswith(f".{COOKIE_DOMAIN}")


def _is_browser_profile_path(path: Path) -> bool:
    normalized = str(path).replace("\\", "/").lower().rstrip("/")
    return any(
        normalized.endswith(marker) or f"{marker}/" in normalized
        for marker in ("/google/chrome/user data", "/microsoft/edge/user data")
    )


class PlaywrightSession:
    """管理一个与现有浏览器配置隔离的 MiMo 持久化会话。

    ``user_data_dir`` 默认位于系统临时目录的专用子目录中；传入路径时，
    调用方应使用专门为本会话准备的目录，不要传 Chrome/Edge 的 User Data 路径。
    """

    def __init__(
        self,
        user_data_dir: str | Path = DEFAULT_USER_DATA_DIR,
        *,
        headless: bool = False,
        launch_options: dict[str, Any] | None = None,
    ) -> None:
        self.user_data_dir = Path(user_data_dir).expanduser()
        self.headless = headless
        self.launch_options = dict(launch_options or {})
        self._playwright: Any = None
        self._context: Any = None

    @property
    def context(self) -> Any:
        """返回已启动的 BrowserContext；未启动时返回 ``None``。"""
        return self._context

    def start(self) -> Any:
        """启动或复用持久化 BrowserContext。"""
        if self._context is not None:
            return self._context

        try:
            if _is_browser_profile_path(self.user_data_dir):
                raise PlaywrightSessionError(
                    "user_data_dir 必须是独立目录，不能使用 Chrome/Edge User Data"
                )
            self.user_data_dir.mkdir(parents=True, exist_ok=True)
            sync_playwright = _load_sync_playwright()
            self._playwright = sync_playwright().start()
            options = dict(self.launch_options)
            options.setdefault("headless", self.headless)
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.user_data_dir), **options
            )
            return self._context
        except PlaywrightSessionError:
            self._close_runtime()
            raise
        except Exception as exc:
            self._close_runtime()
            raise PlaywrightSessionError(
                "Playwright 持久化会话启动失败，"
                f"（{type(exc).__name__}）；请确认 Chromium 已安装且专用目录可写"
            ) from exc

    def open_mimo(
        self,
        url: str = MIMO_URL,
        *,
        wait_until: str = "domcontentloaded",
        timeout: float = 30_000,
    ) -> Any:
        """打开 MiMo 平台页面并返回 Page。"""
        context = self.start()
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return page
        except Exception as exc:
            raise PlaywrightSessionError(
                f"打开 MiMo 平台页面失败（{type(exc).__name__}）"
            ) from exc

    def get_cookie(self) -> str:
        """读取 xiaomimimo.com Cookie，并拼成请求头格式字符串。"""
        context = self.start()
        try:
            cookies = context.cookies()
            matched = [
                cookie
                for cookie in cookies
                if _is_mimo_domain(cookie.get("domain"))
                and cookie.get("name") is not None
                and cookie.get("value") is not None
            ]
        except Exception as exc:
            raise PlaywrightSessionError(
                f"读取 MiMo Cookie 失败（{type(exc).__name__}）"
            ) from exc

        if not matched:
            raise PlaywrightSessionError(
                "未找到 xiaomimimo.com Cookie，请先在会话页面登录 MiMo"
            )
        return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in matched)

    def refresh_cookie(
        self,
        *,
        interactive: bool = False,
        timeout_seconds: int = 30,
    ) -> str:
        """打开 MiMo 页面并读取 Cookie；interactive 模式显示浏览器窗口。"""
        if timeout_seconds <= 0:
            raise PlaywrightSessionError("Cookie 刷新超时必须大于 0 秒")
        self.headless = not interactive
        self.open_mimo(timeout=timeout_seconds * 1000)
        if not interactive:
            return self.get_cookie()

        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            try:
                return self.get_cookie()
            except PlaywrightSessionError as exc:
                last_error = str(exc)
                time.sleep(1)
        raise PlaywrightSessionError(
            last_error or "等待 MiMo 登录状态超时，请完成登录或验证码验证"
        )

    def _close_runtime(self) -> None:
        """尽力释放运行时对象，不把清理异常覆盖原始错误。"""
        context, playwright = self._context, self._playwright
        self._context = None
        self._playwright = None
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def close(self, *, remove_user_data_dir: bool = False) -> None:
        """关闭会话；可选删除专用 user_data_dir。"""
        self._close_runtime()
        if remove_user_data_dir:
            try:
                shutil.rmtree(self.user_data_dir)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise PlaywrightSessionError(
                    f"清理 Playwright 会话目录失败（{type(exc).__name__}）"
                ) from exc

    cleanup = close

    def __enter__(self) -> "PlaywrightSession":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


def refresh_cookie(
    interactive: bool = False,
    timeout_seconds: int = 30,
) -> tuple[str | None, str | None]:
    """使用项目专用目录刷新 MiMo Cookie，返回 ``(Cookie, 错误)``。"""
    if timeout_seconds <= 0:
        return None, "Cookie 刷新超时必须大于 0 秒"

    session: PlaywrightSession | None = None
    try:
        from config import playwright_user_data_dir

        session = PlaywrightSession(
            playwright_user_data_dir(), headless=not interactive
        )
        return session.refresh_cookie(
            interactive=interactive,
            timeout_seconds=timeout_seconds,
        ), None
    except PlaywrightSessionError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"刷新 MiMo Cookie 失败（{type(exc).__name__}）"
    finally:
        if session is not None:
            session.close()
