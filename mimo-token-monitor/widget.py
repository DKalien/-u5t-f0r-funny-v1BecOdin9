from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QAction, QFont
from PyQt6.QtWidgets import (
    QWidget, QMenu, QDialog, QFormLayout,
    QLineEdit, QSpinBox, QDoubleSpinBox, QPushButton,
    QHBoxLayout, QLabel, QMessageBox, QApplication,
)
from datetime import datetime
import json
import api_client
from config import save_config
import snapshot_writer

SNAP_THRESHOLD = 15  # px, 距屏幕边缘多少像素内触发吸附

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


# ── Settings dialog ─────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MiMo Token 设置")
        self.setFixedSize(500, 260)
        self.cfg = dict(cfg)

        layout = QFormLayout(self)

        self.cookie_edit = QLineEdit(cfg.get("cookie", ""))
        self.cookie_edit.setPlaceholderText("从浏览器 F12 → Application → Cookies 复制完整 cookie 字符串")
        layout.addRow("Cookie:", self.cookie_edit)

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

        self.snapshot_edit = QLineEdit(cfg.get("snapshot_path", ""))
        self.snapshot_edit.setPlaceholderText("留空禁用 | 例: ~/.claude/plugins/claude-hud/mimo-snapshot.json")
        layout.addRow("快照路径:", self.snapshot_edit)

        hint = QLabel("获取 Cookie: 浏览器打开 platform.xiaomimimo.com 并登录 →\nF12 → Network → 刷新页面 → 点任意请求 → 复制 Cookie 头\n\n快照路径: 填写后会生成 JSON 供 claude-hud 读取显示用量")
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

    def get_config(self) -> dict:
        self.cfg["cookie"] = self.cookie_edit.text().strip()
        self.cfg["refresh_interval"] = self.interval_spin.value()
        self.cfg["opacity"] = self.opacity_spin.value()
        self.cfg["snapshot_path"] = self.snapshot_edit.text().strip()
        return self.cfg


# ── Main widget ─────────────────────────────────────────────────
class TokenWidget(QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._drag_pos = QPoint()
        self._last_error = ""
        self._last_update = "等待更新..."

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

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(260)
        self.setFixedHeight(140)
        pos = cfg.get("position", [100, 100])
        self.move(pos[0], pos[1])
        self.setWindowOpacity(cfg.get("opacity", 0.85))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._do_fetch)
        interval_ms = cfg.get("refresh_interval", 300) * 1000
        self._timer.start(interval_ms)

        QTimer.singleShot(500, self._do_fetch)

    # ── Painting ────────────────────────────────────────────────
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        p.setBrush(QBrush(BG_COLOR))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 12, 12)

        # Title + balance
        font_title = QFont("Microsoft YaHei", 10, QFont.Weight.Bold)
        p.setFont(font_title)
        p.setPen(QPen(TEXT_COLOR))
        p.drawText(16, 22, "MiMo Token")

        # Balance on the right
        if self._balance is not None and self._balance != 0:
            p.setPen(QPen(ACCENT_GREEN))
            p.drawText(150, 22, _fmt_money(self._balance))

        # Plan info
        font_small = QFont("Microsoft YaHei", 9)
        p.setFont(font_small)
        p.setPen(QPen(TEXT_COLOR))

        if self._plan_total > 0:
            pct = self._plan_used / self._plan_total
            pct_text = f"{pct * 100:.1f}%"

            p.drawText(16, 42, "Token Plan")
            p.drawText(200, 42, pct_text)

            bar_x, bar_y, bar_w, bar_h = 16, 50, 228, 14
            p.setBrush(QBrush(BAR_BG))
            p.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 4, 4)
            p.setBrush(QBrush(_bar_color(1 - pct)))
            p.drawRoundedRect(bar_x, bar_y, int(bar_w * min(pct, 1.0)), bar_h, 4, 4)

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

            # Estimated days remaining
            remaining = self._plan_total - self._plan_used
            if self._month_used > 0 and remaining > 0:
                from datetime import datetime
                day_of_month = datetime.now().day
                if day_of_month > 0:
                    daily_rate = self._month_used / day_of_month
                    if daily_rate > 0:
                        days_left = int(remaining / daily_rate)
                        p.setPen(QPen(DIM))
                        p.drawText(16, 124, f"按当前速率约可用 {days_left} 天")

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
        p.setPen(QPen(DIM))
        font_tiny = QFont("Microsoft YaHei", 7)
        p.setFont(font_tiny)
        if self._last_error:
            p.setPen(QPen(ACCENT_RED))
            p.drawText(16, 134, self._last_error[:50])
        else:
            p.drawText(180, 134, f"更新于 {self._last_update}")

        p.end()

    # ── Mouse events ────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
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

                if x < geo.left() + SNAP_THRESHOLD:
                    x = geo.left()
                elif x + w > geo.right() - SNAP_THRESHOLD:
                    x = geo.right() - w

                if y < geo.top() + SNAP_THRESHOLD:
                    y = geo.top()
                elif y + h > geo.bottom() - SNAP_THRESHOLD:
                    y = geo.bottom() - h

                new_pos = QPoint(x, y)

            self.move(new_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.cfg["position"] = [self.x(), self.y()]
            save_config(self.cfg)

    def mouseDoubleClickEvent(self, e):
        self._do_fetch()

    def contextMenuEvent(self, e):
        menu = QMenu(self)

        refresh_act = QAction("刷新", self)
        refresh_act.triggered.connect(self._do_fetch)
        menu.addAction(refresh_act)

        settings_act = QAction("设置", self)
        settings_act.triggered.connect(self._open_settings)
        menu.addAction(settings_act)

        debug_act = QAction("查看原始数据", self)
        debug_act.triggered.connect(self._show_debug)
        menu.addAction(debug_act)

        menu.addSeparator()

        quit_act = QAction("退出", self)
        quit_act.triggered.connect(QApplication.quit)
        menu.addAction(quit_act)

        menu.exec(e.globalPos())

    # ── Fetch ───────────────────────────────────────────────────
    def _do_fetch(self):
        if hasattr(self, "_worker") and self._worker.isRunning():
            return

        cookie = self.cfg.get("cookie", "")
        if not cookie:
            self._last_error = "请先在设置中填入 Cookie"
            self.update()
            return

        self._worker = FetchWorker(cookie)
        self._worker.finished.connect(self._on_fetch_done)
        self._worker.start()

    def _on_fetch_done(self, bal_result: dict, usage_result: dict):
        if not bal_result["ok"]:
            self._last_error = bal_result.get("error", "余额查询失败")
        elif not usage_result["ok"]:
            self._last_error = usage_result.get("error", "用量查询失败")
        else:
            self._last_error = ""

        # Parse balance
        if bal_result["ok"] and bal_result["balance"] is not None:
            self._balance = float(bal_result["balance"])

        # Parse usage / plan info
        if usage_result["ok"] and usage_result["data"]:
            self._parse_plan(usage_result["data"])

        self._last_update = datetime.now().strftime("%H:%M:%S")
        self._update_tooltip(bal_result, usage_result)
        self._write_snapshot()
        self.update()

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

    def _update_tooltip(self, bal_result, usage_result):
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
            # Estimate days remaining at current burn rate
            if self._month_used > 0 and self._month_limit > 0:
                from datetime import datetime
                day_of_month = datetime.now().day
                if day_of_month > 0:
                    daily_rate = self._month_used / day_of_month
                    if daily_rate > 0:
                        days_left = remaining / daily_rate
                        lines.append(f"预计可用: {int(days_left)} 天")
        if self._month_limit > 0:
            m_pct = self._month_used / self._month_limit * 100
            lines.append(f"本月: {_fmt_tokens(self._month_used)} / {_fmt_tokens(self._month_limit)} ({m_pct:.1f}%)")
        if self._last_error:
            lines.append(f"错误: {self._last_error}")
        self.setToolTip("\n".join(lines))

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
            error=self._last_error if self._last_error else None,
        )

    # ── Actions ─────────────────────────────────────────────────
    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cfg = dlg.get_config()
            save_config(self.cfg)
            self.setWindowOpacity(self.cfg.get("opacity", 0.85))
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
