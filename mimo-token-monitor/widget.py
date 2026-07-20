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


# ── Settings dialog ─────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MiMo Token 设置")
        self.setFixedSize(500, 370)
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

        hint = QLabel(
            "自动导入: Edge 快捷方式末尾加 --remote-debugging-port=9222 --remote-allow-origins=*，重启浏览器后点击按钮\n"
            "手动导入: F12 → Network → 刷新页面 → 点任意请求 → 复制 Cookie 头\n\n"
            "有效期至: 手动填写套餐到期日期，例如 2026-08-31\n"
            "快照路径: 填写后会生成 JSON 供 claude-hud 读取显示用量"
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
        # Daily usage
        self._daily_used = 0  # Today's token usage

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self.setFixedWidth(260)
        self.setFixedHeight(140)
        pos = cfg.get("position", [100, 100])
        self.move(pos[0], pos[1])
        self.setWindowOpacity(cfg.get("opacity", 0.85))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._do_fetch)
        interval_ms = cfg.get("refresh_interval", 300) * 1000
        self._timer.start(interval_ms)

        # 系统托盘
        self._setup_tray()

        QTimer.singleShot(500, self._do_fetch)

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

        # Title + balance
        font_title = QFont("Microsoft YaHei", 10, QFont.Weight.Bold)
        p.setFont(font_title)
        p.setPen(QPen(TEXT_COLOR))
        p.drawText(16, 22, "MiMo Token")

        # Balance on the right
        if self._balance is not None and self._balance != 0:
            p.setPen(QPen(ACCENT_GREEN))
            p.drawText(150, 22, _fmt_money(self._balance))

        # 刷新按钮（从浏览器导入）
        self._refresh_btn_rect = self._draw_refresh_button(p)

        # 最小化按钮（右上角 ─ 符号）
        self._minimize_btn_rect = self._draw_minimize_button(p)

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

            fill_w = int(bar_w * min(pct, 1.0))
            if fill_w > 0:
                # 当填充宽度较小时，限制圆角半径，避免超出外框圆角范围
                fill_radius = min(4, fill_w // 2)
                p.setBrush(QBrush(_bar_color(1 - pct)))
                # 填充自身的圆角在宽度很小时会变成方角，可能露到外框的
                # 圆角区域之外；将填充裁剪到外框路径内，确保四角始终对齐。
                p.save()
                bar_path = QPainterPath()
                bar_path.addRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 4, 4)
                p.setClipPath(bar_path)
                p.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, fill_radius, fill_radius)
                p.restore()

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
        p.setPen(QPen(DIM))
        font_tiny = QFont("Microsoft YaHei", 7)
        p.setFont(font_tiny)
        if self._last_error:
            p.setPen(QPen(ACCENT_RED))
            p.drawText(16, 134, self._last_error[:50])
        else:
            p.drawText(180, 134, f"更新于 {self._last_update}")

        p.end()

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

    # ── Mouse events ────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
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
        else:
            # 鼠标悬停时更新按钮高亮状态，并切换光标
            pos = e.position().toPoint()
            on_btn = False
            if hasattr(self, '_minimize_btn_rect') and self._minimize_btn_rect.contains(pos):
                on_btn = True
            if hasattr(self, '_refresh_btn_rect') and self._refresh_btn_rect.contains(pos):
                on_btn = True
            new_cursor = Qt.CursorShape.PointingHandCursor if on_btn else Qt.CursorShape.ArrowCursor
            if self.cursor().shape() != new_cursor:
                self.setCursor(QCursor(new_cursor))
                self.update()

    def mouseReleaseEvent(self, e):
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

    def _quit_app(self):
        """真正退出应用程序。"""
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
            # Update daily baseline after parsing month usage
            self._update_daily_baseline()

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
            # Daily usage
            if self._daily_used > 0:
                lines.append(f"今日已用 {_fmt_tokens(self._daily_used)}")
            expiry_date = _format_expiry(self.cfg.get("expiry_date", ""))
            lines.append(f"有效期至: {expiry_date}")
        if self._month_limit > 0:
            m_pct = self._month_used / self._month_limit * 100
            lines.append(f"本月: {_fmt_tokens(self._month_used)} / {_fmt_tokens(self._month_limit)} ({m_pct:.1f}%)")
        if self._last_error:
            lines.append(f"错误: {self._last_error}")
        tooltip_text = "\n".join(lines)
        self.setToolTip(tooltip_text)
        # 同步更新托盘图标 tooltip
        if hasattr(self, '_tray_icon'):
            # 系统托盘 tooltip 最长 128 字符（Windows 限制）
            self._tray_icon.setToolTip(tooltip_text[:128] if len(tooltip_text) > 128 else tooltip_text)

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
            error=self._last_error if self._last_error else None,
        )

    # ── Actions ─────────────────────────────────────────────────
    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
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
