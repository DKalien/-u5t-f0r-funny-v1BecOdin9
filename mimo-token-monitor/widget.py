from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint, QRect, QRectF, QPointF
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QAction, QFont, QIcon, QCursor, QPolygonF, QPainterPath
from PyQt6.QtWidgets import (
    QWidget, QMenu, QDialog, QFormLayout,
    QLineEdit, QSpinBox, QDoubleSpinBox, QPushButton,
    QCheckBox, QHBoxLayout, QLabel, QMessageBox, QApplication, QSystemTrayIcon,
)
from datetime import datetime
import json
import math
import os
import sys
import api_client
from config import save_config
import cookie_reader
import snapshot_writer
import window_snap

SNAP_THRESHOLD = 15  # px, 距屏幕边缘多少像素内触发吸附

MIMO_MODE = "mimo"
THIRD_PARTY_MODE = "third_party"
OVERVIEW_MODE = "overview"

BASE_HEIGHT = 140
# Overview keeps the same compact footprint as the other display modes.
OVERVIEW_HEIGHT = BASE_HEIGHT

# ── Colors ──────────────────────────────────────────────────────
BG_COLOR = QColor(30, 30, 30, 220)
TEXT_COLOR = QColor(230, 230, 230)
ACCENT_GREEN = QColor(76, 175, 80)
ACCENT_YELLOW = QColor(255, 193, 7)
ACCENT_RED = QColor(244, 67, 54)
BAR_BG = QColor(60, 60, 60)
DIM = QColor(150, 150, 150)


def _bar_color(pct: float) -> QColor:
    if pct > 0.5:
        return ACCENT_GREEN
    if pct > 0.2:
        return ACCENT_YELLOW
    return ACCENT_RED


def _fmt_tokens(n) -> str:
    if n is None:
        return "--"
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_money(v) -> str:
    if v is None:
        return "--"
    return f"¥{float(v):.2f}"


def _expiry_days_left(expiry_date):
    """Return days until the user-entered expiry date, or None if invalid."""
    expiry_text = str(expiry_date or "").strip()
    if not expiry_text:
        return None

    try:
        expiry = datetime.strptime(expiry_text, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (expiry - datetime.now().date()).days


def _format_expiry(expiry_date) -> str:
    """Format the user-entered expiry date and add a short-term reminder."""
    expiry_text = str(expiry_date or "").strip()
    if not expiry_text:
        return "未设置"

    days_left = _expiry_days_left(expiry_text)
    if days_left is None:
        return expiry_text
    if 0 <= days_left < 7:
        return f"{expiry_text}，还剩{days_left}天"
    return expiry_text


# ── Plan tier definitions ──────────────────────────────────────
PLAN_TIERS = [
    (82_000_000_000, "Max", 659),
    (38_000_000_000, "Pro", 329),
    (11_000_000_000, "Standard", 99),
    (4_100_000_000, "Lite", 39),
]


def _get_plan_tier_info(total: int):
    """Return (tier_name, monthly_price, cost_per_credit) based on total plan tokens."""
    for credits, name, price in sorted(PLAN_TIERS, reverse=True):
        if total >= credits * 0.95:
            return name, price, price / credits
    return None, None, None
# ── Probe thread ────────────────────────────────────────────────
class FetchWorker(QThread):
    finished = pyqtSignal(dict, dict)

    def __init__(self, cookie):
        super().__init__()
        self.cookie = cookie

    def run(self):
        bal = api_client.fetch_balance(self.cookie)
        usage = api_client.fetch_usage(self.cookie)
        self.finished.emit(bal, usage)



# ── Third-party usage probe thread ──────────────────────────────
class ThirdPartyFetchWorker(QThread):
    """Background thread for third-party usage API fetch."""
    finished = pyqtSignal(dict)

    def __init__(self, base_url: str, api_key: str, window: str = "7d"):
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key
        self.window = window

    def run(self):
        result = api_client.fetch_third_party_usage(
            self.base_url, self.api_key, self.window,
        )
        self.finished.emit(result)

# -- GPT weekly usage probe thread --
class GPTFetchWorker(QThread):
    finished = pyqtSignal(dict)

    def __init__(self, session_cookie=""):
        super().__init__()
        self.session_cookie = session_cookie

    def run(self):
        result = api_client.fetch_gpt_weekly_usage(self.session_cookie)
        self.finished.emit(result)


# ── Settings dialog ─────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MiMo Token 设置")
        self.setFixedSize(500, 520)
        self.cfg = dict(cfg)

        layout = QFormLayout(self)

        self.cookie_edit = QLineEdit(cfg.get("cookie", ""))
        self.cookie_edit.setPlaceholderText("手动粘贴或点击右侧按钮从浏览器自动导入")

        import_btn = QPushButton("从浏览器导入")
        import_btn.setFixedWidth(110)
        import_btn.clicked.connect(self._import_cookie)

        cookie_row = QHBoxLayout()
        cookie_row.addWidget(self.cookie_edit)
        cookie_row.addWidget(import_btn)
        layout.addRow("Cookie:", cookie_row)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(60, 3600)
        self.interval_spin.setSingleStep(60)
        self.interval_spin.setValue(cfg.get("refresh_interval", 300))
        self.interval_spin.setSuffix(" 秒")
        layout.addRow("刷新间隔:", self.interval_spin)

        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.3, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setValue(cfg.get("opacity", 0.85))
        layout.addRow("透明度:", self.opacity_spin)

        self.expiry_edit = QLineEdit(str(cfg.get("expiry_date", "") or ""))
        self.expiry_edit.setPlaceholderText("例如 2026-08-31")
        layout.addRow("有效期至:", self.expiry_edit)

        self.expiry_alert_check = QCheckBox("有效期小于等于 3 天时显示红色")
        self.expiry_alert_check.setChecked(cfg.get("expiry_alert_enabled", True))
        layout.addRow("到期提醒:", self.expiry_alert_check)

        self.snapshot_edit = QLineEdit(cfg.get("snapshot_path", ""))
        self.snapshot_edit.setPlaceholderText("留空禁用 | 例: ~/.claude/plugins/claude-hud/mimo-snapshot.json")
        layout.addRow("快照路径:", self.snapshot_edit)

        # third-party usage settings
        self.tp_base_url_edit = QLineEdit(cfg.get("third_party_base_url", "http://codex.wlbclub.com"))
        self.tp_base_url_edit.setPlaceholderText("可填主域名、/v1 或完整 /v1/usage，程序会自动规范")
        layout.addRow("WLB Base URL:", self.tp_base_url_edit)

        self.tp_api_key_edit = QLineEdit(cfg.get("third_party_api_key", ""))
        self.tp_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.tp_api_key_edit.setPlaceholderText("来自 CC Switch 的 API Key")
        layout.addRow("WLB API Key:", self.tp_api_key_edit)
        self.gpt_session_cookie_edit = QLineEdit(cfg.get("gpt_session_cookie", ""))
        self.gpt_session_cookie_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.gpt_session_cookie_edit.setPlaceholderText("可选: ChatGPT session-token cookie，用于获取周限额")
        layout.addRow("GPT Session Cookie:", self.gpt_session_cookie_edit)

        hint = QLabel(
            "自动导入: Edge 快捷方式末尾加 --remote-debugging-port=9222 --remote-allow-origins=*，重启浏览器后点击按钮\n"
            "手动导入: F12 → Network → 刷新页面 → 点任意请求 → 复制 Cookie 头\n\n"
            "有效期至: 手动填写套餐到期日期，例如 2026-08-31\n"
            "快照路径: 填写后会生成 JSON 供 claude-hud 读取显示用量\n"
            "WLB: 填写 Base URL 和 API Key 后可点击标题栏切换图标显示第三方用量\n"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow(hint)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("保存")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addRow(btn_row)

    def _import_cookie(self):
        cookie_str, error = cookie_reader.import_cookie_from_browser()
        if not cookie_str:
            QMessageBox.warning(self, "导入失败", error or "无法从浏览器读取 Cookie")
            return

        # 验证 cookie 是否有效
        result = api_client.fetch_balance(cookie_str)
        if result["ok"]:
            self.cookie_edit.setText(cookie_str)
            QMessageBox.information(self, "导入成功", "Cookie 已从浏览器读取并验证有效")
        elif "过期" in (result.get("error") or ""):
            QMessageBox.warning(
                self, "Cookie 已过期",
                "浏览器中的 Cookie 也已过期，请先在浏览器中重新登录\n"
                "platform.xiaomimimo.com，然后重试",
            )
        else:
            # 网络错误等：仍然填入，让用户自行判断
            self.cookie_edit.setText(cookie_str)
            QMessageBox.warning(
                self, "导入成功但验证失败",
                f"Cookie 已读取，但验证时出错：{result.get('error', '未知错误')}\n已填入，请手动确认",
            )

    def get_config(self) -> dict:
        self.cfg["cookie"] = self.cookie_edit.text().strip()
        self.cfg["refresh_interval"] = self.interval_spin.value()
        self.cfg["opacity"] = self.opacity_spin.value()
        self.cfg["expiry_date"] = self.expiry_edit.text().strip()
        self.cfg["expiry_alert_enabled"] = self.expiry_alert_check.isChecked()
        self.cfg["snapshot_path"] = self.snapshot_edit.text().strip()
        self.cfg["third_party_base_url"] = self.tp_base_url_edit.text().strip() or "http://codex.wlbclub.com"
        self.cfg["third_party_api_key"] = self.tp_api_key_edit.text().strip()
        self.cfg["gpt_session_cookie"] = self.gpt_session_cookie_edit.text().strip()
        return self.cfg


# ── Main widget ─────────────────────────────────────────────────
class TokenWidget(QWidget):
    def __init__(self, cfg: dict, exit_callback=None, startup_sync_result=None):
        super().__init__()
        self.cfg = cfg
        self._exit_callback = exit_callback
        self._exit_requested = False
        self._drag_pos = QPoint()
        self._last_error = ""
        self._mimo_error = ""
        self._tp_error = ""
        # GPT weekly usage state
        self._gpt_data = None
        self._gpt_error = ""
        self._last_update = "等待更新..."
        self._pin_btn_rect = QRect()  # placeholder, set in paintEvent

        # Data from API
        self._balance = None       # float, yuan
        self._plan_total = 0       # total plan credits (limit)
        self._plan_used = 0        # total plan used
        self._month_used = 0       # this month used
        self._month_limit = 0      # this month limit
        # Pay-as-you-go
        self._payg_tokens = 0
        self._payg_input = 0
        self._payg_output = 0
        self._payg_total_cost = None
        self._payg_month_cost = None
        # Daily usage
        self._daily_used = 0  # Today's token usage

        # Third-party usage state
        self._tp_data = None
        self._switch_rect = QRect()

        # Always-on-top: controlled by cfg; default True for backward compat
        self._always_on_top = cfg.get("always_on_top", True)
        _flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._always_on_top:
            _flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(_flags)
        self.setWindowTitle("MiMo Token Monitor")  # native title for Win32 EnumWindows
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setFixedWidth(260)
        self._apply_size_for_mode(cfg.get("display_mode", MIMO_MODE))
        pos = self._resolve_start_position(cfg.get("position", [100, 100]))
        self.move(*pos)
        if cfg.get("position") != list(pos):
            cfg["position"] = list(pos)
            save_config(cfg)
        self.setWindowOpacity(cfg.get("opacity", 0.85))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._do_fetch)
        interval_ms = cfg.get("refresh_interval", 300) * 1000
        self._timer.start(interval_ms)

        # 系统托盘
        self._setup_tray()

        if startup_sync_result is not None and not startup_sync_result.ok:
            QTimer.singleShot(0, lambda: self.show_sync_result(startup_sync_result))

        QTimer.singleShot(500, self._do_fetch)

    def _resolve_start_position(self, raw_position) -> tuple[int, int]:
        """Keep a previously saved position visible after monitor changes."""
        try:
            position = (int(raw_position[0]), int(raw_position[1]))
        except (TypeError, ValueError, IndexError):
            position = (100, 100)

        window_rect = QRect(position[0], position[1], self.width(), self.height())
        minimum_visible = 20
        for screen in QApplication.screens():
            visible_part = window_rect.intersected(screen.availableGeometry())
            if (
                visible_part.width() >= minimum_visible
                and visible_part.height() >= minimum_visible
            ):
                return position

        screen = QApplication.primaryScreen()
        if screen is None:
            return (100, 100)

        available = screen.availableGeometry()
        margin = 20
        x = available.left() + min(
            100, max(0, available.width() - self.width() - margin)
        )
        y = available.top() + min(
            100, max(0, available.height() - self.height() - margin)
        )
        return (x, y)

    def _apply_size_for_mode(self, mode: str) -> None:
        """Resize the window consistently when switching display modes."""
        target_height = OVERVIEW_HEIGHT if mode == OVERVIEW_MODE else BASE_HEIGHT
        if self.height() == target_height:
            return
        top_left = self.geometry().topLeft()
        self.setFixedHeight(target_height)
        screen = QApplication.screenAt(top_left)
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        x = max(geometry.left(), min(top_left.x(), geometry.right() - self.width() + 1))
        y = max(geometry.top(), min(top_left.y(), geometry.bottom() - self.height() + 1))
        self.move(x, y)

    @staticmethod
    def _normalize_overview_percent(value):
        try:
            percent = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(percent):
            return None
        return max(0.0, min(100.0, percent))

    @classmethod
    def _format_overview_percent(cls, value, configured: bool) -> str:
        if not configured:
            return "未配置"
        percent = cls._normalize_overview_percent(value)
        if percent is None:
            return "--"
        return f"{percent:.1f}%"

    @staticmethod
    def _overview_row_metrics(index: int):
        """Return geometry for one full-size overview row."""
        row_x = 16
        label_y = 38 + index * 34
        bar_y = 44 + index * 34
        return row_x, label_y, bar_y, 228, 14

    def _build_mimo_tooltip_lines(self) -> list:
        lines = []
        if self._balance is not None and self._balance != 0:
            lines.append(f"余额: {_fmt_money(self._balance)}")
        if self._plan_total > 0:
            pct = self._plan_used / self._plan_total * 100
            remaining = self._plan_total - self._plan_used
            lines.append(f"Token Plan: {pct:.1f}%")
            lines.append(f"已用: {_fmt_tokens(self._plan_used)}")
            lines.append(f"总额: {_fmt_tokens(self._plan_total)}")
            lines.append(f"剩余: {_fmt_tokens(max(0, remaining))}")
            _, _, cost_per_credit = _get_plan_tier_info(self._plan_total)
            if cost_per_credit:
                used_cost = self._plan_used * cost_per_credit
                lines.append(f"已用折合 ≈ ¥{used_cost:.2f}")
            if self._daily_used > 0:
                lines.append(f"今日已用 {_fmt_tokens(self._daily_used)}")
            expiry_date = _format_expiry(self.cfg.get("expiry_date", ""))
            lines.append(f"有效期至: {expiry_date}")
        if self._month_limit > 0:
            m_pct = self._month_used / self._month_limit * 100
            lines.append(f"本月: {_fmt_tokens(self._month_used)} / {_fmt_tokens(self._month_limit)} ({m_pct:.1f}%)")
        return lines

    def _build_third_party_tooltip_lines(self) -> list:
        lines = ["WLB"]
        if self._tp_data:
            d = self._tp_data
            lines.append(f"剩余: {d.get('remaining_percent', 0):.2f}%")
            lines.append(f"已用: {d.get('used_percent', 0):.2f}%")
            lines.append(f"窗口: {d.get('window', '7d')}")
            lines.append(f"总额: {d.get('total_percent', 100)}%")
            lines.append(f"状态: {'Active' if d.get('is_valid') else 'Inactive'}")
        return lines

    def _build_overview_tooltip_lines(self) -> list:
        lines = ["总览"]
        has_cookie = bool(self.cfg.get("cookie", "").strip())
        mimo_pct = (self._plan_used / self._plan_total * 100) if has_cookie and self._plan_total > 0 else None
        lines.append(f"Token Plan: {self._format_overview_percent(mimo_pct, has_cookie)}")
        has_api_key = bool(self.cfg.get("third_party_api_key", "").strip())
        tp_pct = self._tp_data.get("used_percent") if has_api_key and isinstance(self._tp_data, dict) else None
        lines.append(f"WLB: {self._format_overview_percent(tp_pct, has_api_key)}")
        has_gpt = bool(self.cfg.get("gpt_session_cookie", "").strip()) or (isinstance(self._gpt_data, dict) and self._gpt_data.get("used_percent") is not None)
        gpt_pct = self._gpt_data.get("used_percent") if isinstance(self._gpt_data, dict) else None
        lines.append(f"GPT \u5468\u9650\u989d: {self._format_overview_percent(gpt_pct, has_gpt)}")
        return lines

    def _refresh_error_state(self):
        """Expose the error for the active page without losing either source error."""
        display_mode = self.cfg.get("display_mode", MIMO_MODE)
        if display_mode == MIMO_MODE:
            self._last_error = self._mimo_error
        elif display_mode == THIRD_PARTY_MODE:
            self._last_error = self._tp_error
        else:
            self._last_error = self._mimo_error or self._tp_error or self._gpt_error

    def _setup_tray(self):
        """Initialize system tray icon and menu."""
        # 兼容 PyInstaller 打包后的路径
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        icon_path = os.path.join(base_path, "icon.ico")
        if os.path.exists(icon_path):
            self._tray_icon = QSystemTrayIcon(QIcon(icon_path), self)
        else:
            self._tray_icon = QSystemTrayIcon(self)

        # 托盘右键菜单
        tray_menu = QMenu()

        show_act = QAction("显示主窗口", self)
        show_act.triggered.connect(self._show_window)
        tray_menu.addAction(show_act)

        refresh_act = QAction("刷新", self)
        refresh_act.triggered.connect(self._do_fetch)
        tray_menu.addAction(refresh_act)

        import_act = QAction("从浏览器导入", self)
        import_act.triggered.connect(self._import_cookie_quick)
        tray_menu.addAction(import_act)

        tray_menu.addSeparator()

        quit_act = QAction("退出", self)
        quit_act.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_act)

        self._tray_icon.setContextMenu(tray_menu)
        self._tray_icon.setToolTip("MiMo Token Monitor")

        # 双击托盘图标显示/隐藏窗口
        self._tray_icon.activated.connect(self._on_tray_activated)

        self._tray_icon.show()

    # ── Painting ────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        p.setBrush(QBrush(BG_COLOR))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 12, 12)

        # Title + display mode switch icon
        display_mode = self.cfg.get("display_mode", MIMO_MODE)
        font_title = QFont("Microsoft YaHei", 10, QFont.Weight.Bold)
        p.setFont(font_title)
        p.setPen(QPen(TEXT_COLOR))
        title_text = "总览" if display_mode == OVERVIEW_MODE else ("MiMo Token" if display_mode == MIMO_MODE else "WLB")
        p.drawText(16, 22, title_text)

        # Mode switch icon right after title
        fm = p.fontMetrics()
        title_w = int(fm.horizontalAdvance(title_text))
        switch_x = 16 + title_w + 4
        switch_y = 22 - int(fm.ascent())
        switch_h = int(fm.height())
        self._switch_rect = QRect(int(switch_x), int(switch_y), 18, switch_h)
        self._draw_switch_icon(p, self._switch_rect)

        # Balance on the right (MiMo mode only)
        if display_mode == MIMO_MODE and self._balance is not None and self._balance != 0:
            p.setPen(QPen(ACCENT_GREEN))
            p.drawText(150, 22, _fmt_money(self._balance))

        # 置顶按钮
        self._pin_btn_rect = self._draw_pin_button(p)

        # 刷新按钮（从浏览器导入）
        self._refresh_btn_rect = self._draw_refresh_button(p)

        # 最小化按钮（右上角 ─ 符号）
        self._minimize_btn_rect = self._draw_minimize_button(p)

        # Content area
        font_small = QFont("Microsoft YaHei", 9)
        p.setFont(font_small)
        p.setPen(QPen(TEXT_COLOR))

        if display_mode == OVERVIEW_MODE:
            self._paint_overview(p)
        elif display_mode == THIRD_PARTY_MODE:
            self._paint_third_party(p)
        elif self._plan_total > 0:
            pct = self._plan_used / self._plan_total
            pct_text = f"{pct * 100:.1f}%"

            p.drawText(16, 42, "Token Plan")
            p.drawText(200, 42, pct_text)

            self._draw_usage_progress_bar(p, pct)

            p.setPen(QPen(TEXT_COLOR))
            p.drawText(16, 80, f"{_fmt_tokens(self._plan_used)} / {_fmt_tokens(self._plan_total)}")
            remaining = self._plan_total - self._plan_used
            p.drawText(100, 66, 144, 16,
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"剩余: {_fmt_tokens(max(0, remaining))}")

            plan_name, price, cost_per_credit = _get_plan_tier_info(self._plan_total)
            if cost_per_credit:
                used_cost = self._plan_used * cost_per_credit
                p.drawText(16, 82, 228, 16,
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           f"{plan_name} 套餐 ¥{used_cost:.2f} / ¥{price}")

            # Display daily usage (show even if 0)
            if self._plan_total > 0:  # Only show when we have plan data
                p.setPen(QPen(ACCENT_GREEN))
                daily_text = _fmt_tokens(self._daily_used) if self._daily_used > 0 else "0"

                # Calculate daily cost
                _, _, cost_per_credit = _get_plan_tier_info(self._plan_total)
                if cost_per_credit and self._daily_used > 0:
                    daily_cost = self._daily_used * cost_per_credit
                    p.drawText(16, 98, 228, 16,
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               f"今日已用: {daily_text} / ¥{daily_cost:.2f}")
                else:
                    p.drawText(16, 98, 228, 16,
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               f"今日已用: {daily_text}")

            expiry_date = _format_expiry(self.cfg.get("expiry_date", ""))
            expiry_days_left = _expiry_days_left(self.cfg.get("expiry_date", ""))
            alert_enabled = self.cfg.get("expiry_alert_enabled", True)
            expiry_color = (
                ACCENT_RED
                if alert_enabled and expiry_days_left is not None and expiry_days_left <= 3
                else DIM
            )
            p.setPen(QPen(expiry_color))
            p.drawText(16, 124, f"有效期至 {expiry_date}")

        elif self._payg_tokens > 0 or self._payg_total_cost:
            # Pay-as-you-go display
            p.drawText(16, 42, "按量付费")
            p.setPen(QPen(TEXT_COLOR))
            if self._payg_tokens > 0:
                p.drawText(16, 62, f"总用量: {_fmt_tokens(self._payg_tokens)}")
                p.drawText(16, 78, f"输入: {_fmt_tokens(self._payg_input)}  输出: {_fmt_tokens(self._payg_output)}")
            if self._payg_total_cost:
                p.drawText(16, 96, f"总费用: ¥{self._payg_total_cost}")
            if self._payg_month_cost:
                p.setPen(QPen(DIM))
                p.drawText(140, 96, f"本月: ¥{self._payg_month_cost}")

        elif self._balance is not None and self._balance != 0:
            p.drawText(16, 50, f"余额: {_fmt_money(self._balance)}")
            p.setPen(QPen(DIM))
            p.drawText(16, 70, "暂无用量数据")
        else:
            p.setPen(QPen(DIM))
            p.drawText(16, 50, "等待数据...")

        # Update time / error (bottom right)
        status_y = 136 if display_mode == OVERVIEW_MODE else 134
        p.setPen(QPen(DIM))
        font_tiny = QFont("Microsoft YaHei", 7)
        p.setFont(font_tiny)
        if self._last_error:
            p.setPen(QPen(ACCENT_RED))
            p.drawText(16, status_y, self._last_error[:50])
        else:
            p.drawText(180, status_y, f"更新于 {self._last_update}")

        p.end()

    def _draw_usage_progress_bar(self, p: QPainter, used_fraction: float):
        """Draw the shared usage progress bar for MiMo and API Usage modes."""
        bar_x, bar_y, bar_w, bar_h = 16, 50, 228, 14
        usage_fraction = float(used_fraction)

        p.setBrush(QBrush(BAR_BG))
        p.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 4, 4)

        fill_w = int(bar_w * min(usage_fraction, 1.0))
        if fill_w > 0:
            # 当填充宽度较小时，限制圆角半径，避免超出外框圆角范围。
            fill_radius = min(4, fill_w // 2)
            p.setBrush(QBrush(_bar_color(1 - usage_fraction)))

            # 将填充裁剪到外框路径内，确保小比例填充也不会露出外框圆角。
            p.save()
            bar_path = QPainterPath()
            bar_path.addRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 4, 4)
            p.setClipPath(bar_path)
            p.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, fill_radius, fill_radius)
            p.restore()

    def _draw_minimize_button(self, p: QPainter):
        """绘制右上角最小化按钮，返回按钮区域 QRect。"""
        btn_size = 20
        btn_margin = 8
        btn_x = self.width() - btn_size - btn_margin
        btn_y = btn_margin
        btn_rect = QRect(btn_x, btn_y, btn_size, btn_size)

        # 按钮背景（悬停时高亮）
        mouse_pos = self.mapFromGlobal(self.cursor().pos())
        hovered = btn_rect.contains(mouse_pos)
        p.setBrush(QBrush(QColor(255, 255, 255, 40) if hovered else QColor(255, 255, 255, 20)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(btn_rect, 4, 4)

        # 绘制 ─ 符号
        p.setPen(QPen(QColor(190, 190, 190), 2))
        line_y = btn_y + btn_size // 2
        p.drawLine(btn_x + 6, line_y, btn_x + btn_size - 6, line_y)

        return btn_rect

    def _draw_refresh_button(self, p: QPainter):
        """绘制从浏览器导入按钮（刷新图标），返回按钮区域 QRect。"""
        btn_size = 20
        btn_margin = 8
        gap = 4  # 与最小化按钮的间距
        min_btn_x = self.width() - btn_size - btn_margin
        btn_x = min_btn_x - btn_size - gap
        btn_y = btn_margin
        btn_rect = QRect(btn_x, btn_y, btn_size, btn_size)

        # 按钮背景（悬停时高亮）
        mouse_pos = self.mapFromGlobal(self.cursor().pos())
        hovered = btn_rect.contains(mouse_pos)
        p.setBrush(QBrush(QColor(255, 255, 255, 50) if hovered else QColor(255, 255, 255, 20)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(btn_rect, 4, 4)

        # 绘制下载图标：向下箭头 + 底座横线
        cx = int(btn_x + btn_size / 2)
        cy = int(btn_y + btn_size / 2)
        icon_color = QColor(190, 190, 190)

        p.setPen(QPen(icon_color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        # 箭头竖线
        p.drawLine(cx, cy - 4, cx, cy + 2)
        # 箭头两边
        p.drawLine(cx, cy + 2, cx - 3, cy - 1)
        p.drawLine(cx, cy + 2, cx + 3, cy - 1)
        # 底座横线
        p.drawLine(cx - 4, cy + 4, cx + 4, cy + 4)

        return btn_rect

    def _set_always_on_top(self, enabled: bool):
        """Toggle WindowStaysOnTopHint while preserving window position."""
        if self._exit_requested:
            return
        self._always_on_top = enabled
        self.cfg["always_on_top"] = enabled
        save_config(self.cfg)
        pos = self.pos()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if enabled:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.move(pos)
        self.show()
        if enabled:
            self.raise_()
        self.update()

    def _draw_pin_button(self, p: QPainter):
        """Draw pin/unpin toggle button; returns button QRect."""
        btn_size = 20
        btn_margin = 8
        gap = 4  # spacing between buttons
        # pin is leftmost: to the left of the refresh button
        refresh_btn_x = self.width() - btn_size - btn_margin - btn_size - gap
        btn_x = refresh_btn_x - btn_size - gap
        btn_y = btn_margin
        btn_rect = QRect(btn_x, btn_y, btn_size, btn_size)

        # Hover background (same style as minimize/refresh buttons)
        mouse_pos = self.mapFromGlobal(self.cursor().pos())
        hovered = btn_rect.contains(mouse_pos)
        p.setBrush(QBrush(QColor(255, 255, 255, 40) if hovered else QColor(255, 255, 255, 20)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(btn_rect, 4, 4)

        cx = int(btn_x + btn_size / 2)
        cy = int(btn_y + btn_size / 2)

        if self._always_on_top:
            pin_color = QColor(100, 200, 255)  # bright accent when pinned
        else:
            pin_color = QColor(120, 120, 120)  # dim when unpinned

        p.setPen(QPen(pin_color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        # Pin: circle (pin head)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(int(cx - 3), int(cy - 4), 6, 6)
        # Pin: vertical shaft
        p.drawLine(int(cx), int(cy + 2), int(cx), int(cy + 5))
        # Pin: base line
        p.drawLine(int(cx - 4), int(cy + 5), int(cx + 4), int(cy + 5))

        # Unpinned: diagonal slash overlay (red)
        if not self._always_on_top:
            p.setPen(QPen(QColor(244, 67, 54), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(btn_x + 4), int(btn_y + btn_size - 4),
                       int(btn_x + btn_size - 4), int(btn_y + 4))

        return btn_rect

    def _draw_switch_icon(self, p: QPainter, rect: QRect):
        """Draw a compact two-way circular-arrow display mode switch icon."""
        mouse_pos = self.mapFromGlobal(self.cursor().pos())
        hovered = rect.contains(mouse_pos)
        color = QColor(200, 200, 200) if hovered else DIM

        side = min(11, int(rect.width()) - 6, int(rect.height()) - 6)
        icon_rect = QRect(
            int(rect.center().x() - side // 2),
            int(rect.center().y() - side // 2),
            int(side),
            int(side),
        )
        cx = int(icon_rect.center().x())
        cy = int(icon_rect.center().y())

        p.save()
        p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(icon_rect, 35 * 16, 135 * 16)
        p.drawArc(icon_rect, 215 * 16, 135 * 16)

        # Arrowheads point into the two gaps, making the cycle direction clear.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(color))
        p.drawPolygon(QPolygonF([
            QPointF(cx + 3, cy - 4),
            QPointF(cx + 6, cy - 3),
            QPointF(cx + 4, cy - 1),
        ]))
        p.drawPolygon(QPolygonF([
            QPointF(cx - 3, cy + 4),
            QPointF(cx - 6, cy + 3),
            QPointF(cx - 4, cy + 1),
        ]))
        p.restore()

    def _toggle_display_mode(self):
        """Cycle between MiMo Token, API Usage, and overview display modes."""
        current_mode = self.cfg.get("display_mode", MIMO_MODE)
        if current_mode == MIMO_MODE:
            next_mode = THIRD_PARTY_MODE
        elif current_mode == THIRD_PARTY_MODE:
            next_mode = OVERVIEW_MODE
        else:
            next_mode = MIMO_MODE
        self._set_display_mode(next_mode)

    def _set_display_mode(self, mode: str):
        """Switch display mode and refresh."""
        if self._exit_requested:
            return
        self.cfg["display_mode"] = mode
        save_config(self.cfg)
        self._refresh_error_state()
        self._apply_size_for_mode(mode)
        self._do_fetch()
        self.update()

    def _paint_overview(self, p: QPainter):
        """Paint the overview page listing all available data sources."""
        has_cookie = bool(self.cfg.get("cookie", "").strip())
        has_api_key = bool(self.cfg.get("third_party_api_key", "").strip())
        has_gpt = bool(self.cfg.get("gpt_session_cookie", "").strip()) or (isinstance(self._gpt_data, dict) and self._gpt_data.get("used_percent") is not None)

        rows = [
            {
                "name": "Token Plan",
                "configured": has_cookie,
                "percent": (self._plan_used / self._plan_total * 100) if has_cookie and self._plan_total > 0 else None,
            },
            {
                "name": "WLB",
                "configured": has_api_key,
                "percent": self._tp_data.get("used_percent") if has_api_key and isinstance(self._tp_data, dict) else None,
            },
            {
                "name": "GPT \u5468\u9650\u989d",
                "configured": has_gpt,
                "percent": self._gpt_data.get("used_percent") if has_gpt and isinstance(self._gpt_data, dict) else None,
            },
        ]

        for idx, row in enumerate(rows):
            cell_x, label_y, bar_y, bar_w, bar_h = self._overview_row_metrics(idx)

            p.setPen(QPen(TEXT_COLOR))
            label_rect = QRect(cell_x, label_y - 14, bar_w, 18)
            p.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                row["name"],
            )
            p.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._format_overview_percent(row["percent"], row["configured"]),
            )

            p.setBrush(QBrush(BAR_BG))
            p.drawRoundedRect(cell_x, bar_y, bar_w, bar_h, 4, 4)

            percent = self._normalize_overview_percent(row["percent"])
            fraction = 0.0 if percent is None else percent / 100.0
            fill_w = int(bar_w * fraction)
            if fill_w > 0:
                fill_radius = min(4, fill_w // 2)
                p.setBrush(QBrush(_bar_color(1 - fraction)))
                p.save()
                bar_path = QPainterPath()
                bar_path.addRoundedRect(QRectF(cell_x, bar_y, bar_w, bar_h), 4, 4)
                p.setClipPath(bar_path)
                p.drawRoundedRect(cell_x, bar_y, fill_w, bar_h, fill_radius, fill_radius)
                p.restore()

    def _paint_third_party(self, p: QPainter):
        """Paint third-party usage data in the content area."""
        d = self._tp_data or {}
        has_data = self._tp_data is not None
        is_valid = d.get("is_valid", False)
        remaining_pct = d.get("remaining_percent", 0)
        used_pct = d.get("used_percent", 0)

        # Remaining percentage prominently
        p.setPen(QPen(ACCENT_GREEN if has_data and is_valid else DIM))
        font_accent = QFont("Microsoft YaHei", 10, QFont.Weight.Bold)
        p.setFont(font_accent)
        remaining_text = f"剩余 {remaining_pct:.2f}%" if has_data else "剩余 --"
        p.drawText(16, 42, remaining_text)

        # Progress bar (always visible; fill represents used percentage)
        font_small = QFont("Microsoft YaHei", 9)
        p.setFont(font_small)
        used_frac = max(0.0, min(float(used_pct) / 100.0, 1.0)) if has_data else 0.0
        p.setPen(QPen(TEXT_COLOR))
        self._draw_usage_progress_bar(p, used_frac)

        # Details line
        p.setPen(QPen(TEXT_COLOR))
        if has_data:
            details = f"已用 {used_pct:.2f}% | {d.get('window', '7d')} | {d.get('total_percent', 100)}%"
        else:
            details = "等待 API 数据 | 7d | 100%"
        p.drawText(16, 80, details)

        # Status
        status_text = "Active" if is_valid else "Inactive" if has_data else "等待"
        p.setPen(QPen(ACCENT_GREEN if is_valid else ACCENT_RED if has_data else DIM))
        p.drawText(16, 98, f"状态: {status_text}")

    def _do_fetch_third_party(self):
        """Dispatch third-party usage fetch."""
        if self._exit_requested:
            return
        if hasattr(self, "_tp_worker") and self._tp_worker.isRunning():
            return
        api_key = self.cfg.get("third_party_api_key", "")
        if not api_key:
            self._tp_error = "请在设置中配置 WLB API Key"
            self._tp_data = None
            self._refresh_error_state()
            self._apply_overview_tooltip_if_needed()
            self.update()
            return
        base_url = self.cfg.get("third_party_base_url", "http://codex.wlbclub.com")
        self._tp_worker = ThirdPartyFetchWorker(base_url, api_key)
        self._tp_worker.finished.connect(self._on_third_party_fetch_done)
        self._tp_worker.start()

    def _on_third_party_fetch_done(self, result: dict):
        """Handle third-party usage fetch result."""
        if self._exit_requested:
            return
        if result.get("ok"):
            self._tp_data = result.get("data")
            self._tp_error = ""
        else:
            self._tp_error = result.get("error", "第三方用量查询失败")
        self._refresh_error_state()
        self._last_update = datetime.now().strftime("%H:%M:%S")
        self._apply_overview_tooltip_if_needed()
        self.update()

    # ── Mouse events ────────────────────────────────────────────
    def _do_fetch_gpt(self):
        """Dispatch GPT weekly usage fetch."""
        if self._exit_requested:
            return
        if hasattr(self, "_gpt_worker") and self._gpt_worker.isRunning():
            return
        session_cookie = self.cfg.get("gpt_session_cookie", "")
        self._gpt_worker = GPTFetchWorker(session_cookie)
        self._gpt_worker.finished.connect(self._on_gpt_fetch_done)
        self._gpt_worker.start()

    def _on_gpt_fetch_done(self, result: dict):
        """Handle GPT weekly usage fetch result."""
        if self._exit_requested:
            return
        if result.get("ok"):
            self._gpt_data = result.get("data")
            self._gpt_error = ""
        else:
            self._gpt_error = result.get("error", "GPT \u7528\u91cf\u67e5\u8be2\u5931\u8d25")
        self._refresh_error_state()
        self._last_update = datetime.now().strftime("%H:%M:%S")
        self._apply_overview_tooltip_if_needed()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            # 检测是否点击显示模式切换图标
            if hasattr(self, '_switch_rect') and self._switch_rect.contains(e.pos()):
                self._toggle_display_mode()
                e.accept()
                return
            # 检测是否点击置顶按钮
            if hasattr(self, '_pin_btn_rect') and self._pin_btn_rect.contains(e.pos()):
                self._set_always_on_top(not self._always_on_top)
                e.accept()
                return
            # 检测是否点击刷新按钮（从浏览器导入）
            if hasattr(self, '_refresh_btn_rect') and self._refresh_btn_rect.contains(e.pos()):
                self._import_cookie_quick()
                e.accept()
                return
            # 检测是否点击最小化按钮
            if hasattr(self, '_minimize_btn_rect') and self._minimize_btn_rect.contains(e.pos()):
                self.close()
                e.accept()
                return
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            new_pos = e.globalPosition().toPoint() - self._drag_pos

            screen = QApplication.screenAt(e.globalPosition().toPoint())
            if screen:
                geo = screen.availableGeometry()
                w, h = self.width(), self.height()
                x, y = new_pos.x(), new_pos.y()

                geo_right = geo.x() + geo.width()
                geo_bottom = geo.y() + geo.height()
                if x < geo.x() + SNAP_THRESHOLD:
                    x = geo.x()
                elif x + w > geo_right - SNAP_THRESHOLD:
                    x = geo_right - w

                if y < geo.y() + SNAP_THRESHOLD:
                    y = geo.y()
                elif y + h > geo_bottom - SNAP_THRESHOLD:
                    y = geo_bottom - h

                new_pos = QPoint(x, y)

            # Inter-window snap (ETF Tracker / other MiMo instance)
            # Win32 GetWindowRect returns physical pixel coordinates.
            # On dpr>1 monitors these differ from Qt logical coordinates,
            # so we convert to physical space before snapping, then back.
            try:
                own_hwnd = int(self.winId())
                rects = window_snap.get_other_window_rects(own_hwnd)
                if rects:
                    # Determine the screen where the window currently is
                    snap_screen = QApplication.screenAt(new_pos) or screen
                    if snap_screen:
                        scr_geo = snap_screen.geometry()
                        scr_origin = (scr_geo.x(), scr_geo.y())
                        dpr = window_snap.normalize_dpr(snap_screen.devicePixelRatio())
                    else:
                        scr_origin = (0, 0)
                        dpr = 1.0

                    # Convert own position/size to physical pixels
                    phys_pos = window_snap.qt_to_physical_position(
                        (new_pos.x(), new_pos.y()), scr_origin, dpr,
                    )
                    phys_size = window_snap.qt_to_physical_size(
                        (self.width(), self.height()), dpr,
                    )
                    # Threshold in physical pixels
                    phys_threshold = max(1, round(SNAP_THRESHOLD * dpr))

                    # Snap in physical space (rects are already physical)
                    phys_snapped = window_snap.snap_position(
                        phys_pos, phys_size, rects, phys_threshold,
                    )

                    # Convert back to Qt logical coordinates
                    qt_pos = window_snap.physical_to_qt_position(
                        phys_snapped, scr_origin, dpr,
                    )
                    new_pos = QPoint(qt_pos[0], qt_pos[1])
            except Exception:
                pass

            self.move(new_pos)
            e.accept()
        else:
            # 鼠标悬停时更新按钮高亮状态，并切换光标
            pos = e.position().toPoint()
            on_btn = False
            if hasattr(self, '_minimize_btn_rect') and self._minimize_btn_rect.contains(pos):
                on_btn = True
            if hasattr(self, '_refresh_btn_rect') and self._refresh_btn_rect.contains(pos):
                on_btn = True
            if hasattr(self, '_pin_btn_rect') and self._pin_btn_rect.contains(pos):
                on_btn = True
            if hasattr(self, '_switch_rect') and self._switch_rect.contains(pos):
                on_btn = True
            new_cursor = Qt.CursorShape.PointingHandCursor if on_btn else Qt.CursorShape.ArrowCursor
            if self.cursor().shape() != new_cursor:
                self.setCursor(QCursor(new_cursor))
                self.update()

    def mouseReleaseEvent(self, e):
        if self._exit_requested:
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.cfg["position"] = [self.x(), self.y()]
            save_config(self.cfg)

    def mouseDoubleClickEvent(self, e):
        self._do_fetch()

    def closeEvent(self, event):
        """拦截关闭事件，最小化到系统托盘而非退出。"""
        event.ignore()
        self.hide()
        self._tray_icon.showMessage(
            "MiMo Token Monitor",
            "程序已最小化到系统托盘，双击图标可恢复窗口。",
            QSystemTrayIcon.MessageIcon.Information,
            2000
        )

    def _on_tray_activated(self, reason):
        """处理托盘图标点击事件。"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self):
        """显示并激活窗口。"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def show_sync_result(self, result):
        """Show a non-blocking tray notification for a sync result."""
        title = {
            "code_pull": "MiMo 代码仓库同步",
            "code_push": "MiMo 代码仓库同步",
            "startup": "MiMo 启动同步",
        }.get(result.stage, "MiMo 设置同步")
        self._tray_icon.showMessage(
            title,
            result.message,
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def _quit_app(self):
        """真正退出应用程序；同步期间忽略重复请求。"""
        if self._exit_requested:
            return
        self._exit_requested = True
        self.setEnabled(False)
        self._timer.stop()
        if self._exit_callback is not None:
            self._exit_callback()
        else:
            self.finish_quit()

    def finish_quit(self):
        self._tray_icon.hide()
        QApplication.quit()

    def contextMenuEvent(self, e):
        menu = QMenu(self)

        refresh_act = QAction("刷新", self)
        refresh_act.triggered.connect(self._do_fetch)
        menu.addAction(refresh_act)

        import_act = QAction("从浏览器导入", self)
        import_act.triggered.connect(self._import_cookie_quick)
        menu.addAction(import_act)

        settings_act = QAction("设置", self)
        settings_act.triggered.connect(self._open_settings)
        menu.addAction(settings_act)

        debug_act = QAction("查看原始数据", self)
        debug_act.triggered.connect(self._show_debug)
        menu.addAction(debug_act)

        menu.addSeparator()

        quit_act = QAction("退出", self)
        quit_act.triggered.connect(self._quit_app)
        menu.addAction(quit_act)

        menu.exec(e.globalPos())

    # ── Import ──────────────────────────────────────────────────
    def _import_cookie_quick(self):
        """从浏览器快速导入 Cookie，自动保存并刷新数据。"""
        if self._exit_requested:
            return
        cookie_str, error = cookie_reader.import_cookie_from_browser()
        if not cookie_str:
            QMessageBox.warning(self, "导入失败", error or "无法从浏览器读取 Cookie")
            return

        # 验证 cookie 是否有效
        result = api_client.fetch_balance(cookie_str)
        if result["ok"]:
            # 保存到配置并刷新
            self.cfg["cookie"] = cookie_str
            save_config(self.cfg)
            QMessageBox.information(self, "导入成功", "Cookie 已从浏览器读取并验证有效，正在刷新数据...")
            self._do_fetch()
        elif "过期" in (result.get("error") or ""):
            QMessageBox.warning(
                self, "Cookie 已过期",
                "浏览器中的 Cookie 也已过期，请先在浏览器中重新登录\n"
                "platform.xiaomimimo.com，然后重试",
            )
        else:
            # 网络错误等：仍然保存，让用户自行判断
            self.cfg["cookie"] = cookie_str
            save_config(self.cfg)
            QMessageBox.warning(
                self, "导入成功但验证失败",
                f"Cookie 已读取，但验证时出错：{result.get('error', '未知错误')}\n已保存，请手动确认",
            )
            self._do_fetch()

    # ── Fetch ───────────────────────────────────────────────────
    def _do_fetch(self):
        if self._exit_requested:
            return
        display_mode = self.cfg.get("display_mode", MIMO_MODE)
        if display_mode == OVERVIEW_MODE:
            self._do_fetch_mimo()
            self._do_fetch_third_party()
            self._do_fetch_gpt()
            return
        if display_mode == THIRD_PARTY_MODE:
            self._do_fetch_third_party()
            return
        self._do_fetch_mimo()

    def _do_fetch_mimo(self):
        """Dispatch MiMo plan/balance fetch."""
        if self._exit_requested:
            return
        if hasattr(self, "_worker") and self._worker.isRunning():
            return

        cookie = self.cfg.get("cookie", "")
        if not cookie:
            self._mimo_error = "请先在设置中填入 Cookie"
            self._refresh_error_state()
            self._apply_overview_tooltip_if_needed()
            self.update()
            return

        self._worker = FetchWorker(cookie)
        self._worker.finished.connect(self._on_fetch_done)
        self._worker.start()

    def _on_fetch_done(self, bal_result: dict, usage_result: dict):
        if self._exit_requested:
            return
        if not bal_result["ok"]:
            self._mimo_error = bal_result.get("error", "余额查询失败")
        elif not usage_result["ok"]:
            self._mimo_error = usage_result.get("error", "用量查询失败")
        else:
            self._mimo_error = ""
        self._refresh_error_state()

        # Parse balance
        if bal_result["ok"] and bal_result["balance"] is not None:
            self._balance = float(bal_result["balance"])

        # Parse usage / plan info
        if usage_result["ok"] and usage_result["data"]:
            self._parse_plan(usage_result["data"])
            # Update daily baseline after parsing month usage
            self._update_daily_baseline()

        self._last_update = datetime.now().strftime("%H:%M:%S")
        self._apply_overview_tooltip_if_needed()
        self._write_snapshot()
        self.update()

    def _apply_overview_tooltip_if_needed(self):
        """Build tooltip for the current display mode."""
        display_mode = self.cfg.get("display_mode", MIMO_MODE)
        if display_mode == MIMO_MODE:
            lines = self._build_mimo_tooltip_lines()
        elif display_mode == THIRD_PARTY_MODE:
            lines = self._build_third_party_tooltip_lines()
        else:
            lines = self._build_overview_tooltip_lines()
        if display_mode == OVERVIEW_MODE:
            if self._mimo_error:
                lines.append(f"Token Plan 错误: {self._mimo_error}")
            if self._tp_error:
                lines.append(f"WLB 错误: {self._tp_error}")
            if self._gpt_error:
                lines.append(f"GPT \u5468\u9650\u989d \u9519\u8bef: {self._gpt_error}")
        elif self._last_error:
            lines.append(f"错误: {self._last_error}")
        tooltip_text = "\n".join(lines)
        self.setToolTip(tooltip_text)
        if hasattr(self, '_tray_icon'):
            self._tray_icon.setToolTip(tooltip_text[:128] if len(tooltip_text) > 128 else tooltip_text)

    def _parse_plan(self, data):
        """Parse usage info from API response.

        Supports two formats:
        1. Token Plan: {"data": {"usage": {"items": [{"name": "plan_total_token", "used": X, "limit": Y}]}, "monthUsage": {...}}}
        2. Pay-as-you-go: {"data": {"tokenUsage": {"totalToken": X}, "costUsage": {"totalCost": "0.00", "currentMonthCost": "0.00"}}}
        """
        try:
            root = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(root, dict):
                return

            # Format 1: Token Plan
            usage = root.get("usage")
            if usage and isinstance(usage, dict) and usage.get("items"):
                for item in usage["items"]:
                    if item.get("name") == "plan_total_token":
                        self._plan_used = int(item.get("used", 0))
                        self._plan_total = int(item.get("limit", 0))
                        break

            month = root.get("monthUsage")
            if month and isinstance(month, dict) and month.get("items"):
                mi = month["items"][0]
                self._month_used = int(mi.get("used", 0))
                self._month_limit = int(mi.get("limit", 0))

            # Format 2: Pay-as-you-go token usage
            token_usage = root.get("tokenUsage")
            if token_usage and isinstance(token_usage, dict):
                total = token_usage.get("totalToken")
                if total is not None:
                    self._payg_tokens = int(total)
                inp = token_usage.get("inputToken", 0)
                out = token_usage.get("outputToken", 0)
                self._payg_input = int(inp or 0)
                self._payg_output = int(out or 0)

            cost = root.get("costUsage")
            if cost and isinstance(cost, dict):
                self._payg_total_cost = cost.get("totalCost")
                self._payg_month_cost = cost.get("currentMonthCost")

        except Exception:
            pass

    def _update_daily_baseline(self):
        """Update daily usage baseline and calculate today's usage.

        Logic:
        - Check if today is a new day
        - If new day, save current month_usage as baseline
        - Calculate today's usage = current month_usage - baseline
        """
        today = datetime.now().strftime("%Y-%m-%d")
        baseline_date = self.cfg.get("daily_baseline_date", "")
        baseline_usage = self.cfg.get("daily_baseline_usage", 0)

        # Check if it's a new day or baseline is not set
        if baseline_date != today:
            # New day: save current month_used as baseline
            self.cfg["daily_baseline_date"] = today
            self.cfg["daily_baseline_usage"] = self._month_used
            save_config(self.cfg)
            baseline_usage = self._month_used

        # Calculate today's usage
        # Handle case where month_used might reset (new billing cycle)
        if self._month_used >= baseline_usage:
            self._daily_used = self._month_used - baseline_usage
        else:
            # Month reset (new billing cycle), reset baseline
            self.cfg["daily_baseline_usage"] = self._month_used
            save_config(self.cfg)
            self._daily_used = 0
    # ── Snapshot ─────────────────────────────────────────────────
    def _write_snapshot(self):
        """Write usage snapshot for claude-hud to read."""
        snapshot_path = self.cfg.get("snapshot_path", "")
        if not snapshot_path:
            return

        snapshot_writer.write_snapshot(
            snapshot_path=snapshot_path,
            balance=self._balance,
            plan_used=self._plan_used,
            plan_total=self._plan_total,
            month_used=self._month_used,
            month_limit=self._month_limit,
            daily_used=self._daily_used,
            expiry_date=self.cfg.get("expiry_date", ""),
            error=self._mimo_error if self._mimo_error else None,
        )

    # ── Actions ─────────────────────────────────────────────────
    def _open_settings(self):
        if self._exit_requested:
            return
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if self._exit_requested:
                return
            self.cfg = dlg.get_config()
            save_config(self.cfg)
            self.setWindowOpacity(self.cfg.get("opacity", 0.85))
            self.update()
            interval_ms = self.cfg.get("refresh_interval", 300) * 1000
            self._timer.setInterval(interval_ms)
            self._do_fetch()

    def _show_debug(self):
        """Show raw API response for debugging."""
        cookie = self.cfg.get("cookie", "")
        if not cookie:
            QMessageBox.information(self, "调试", "请先配置 Cookie")
            return
        bal = api_client.fetch_balance(cookie)
        usage = api_client.fetch_usage(cookie)
        usage_url = usage.get("url", "未找到")
        text = f"=== 余额 ===\n{json.dumps(bal, indent=2, ensure_ascii=False)}\n\n=== 用量 (端点: {usage_url}) ===\n{json.dumps(usage, indent=2, ensure_ascii=False)}"
        QMessageBox.information(self, "原始 API 数据", text[:2000])
