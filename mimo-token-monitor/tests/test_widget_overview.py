# encoding: utf-8
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

# Ensure mimo-token-monitor package is importable
_root = pathlib.Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Isolate MIMO_TOKEN_MONITOR_DATA_DIR so save_config never writes production settings.db
_orig_data_dir = os.environ.get("MIMO_TOKEN_MONITOR_DATA_DIR")
_test_data_tmp = tempfile.mkdtemp(prefix="mimo_widget_test_")
os.environ["MIMO_TOKEN_MONITOR_DATA_DIR"] = _test_data_tmp


def setUpModule():
    """Ensure the data-dir env var points to a temp directory before any config import."""
    os.environ["MIMO_TOKEN_MONITOR_DATA_DIR"] = _test_data_tmp


def tearDownModule():
    """Restore the original env var and clean up the temp directory."""
    import shutil
    if _orig_data_dir is not None:
        os.environ["MIMO_TOKEN_MONITOR_DATA_DIR"] = _orig_data_dir
    else:
        os.environ.pop("MIMO_TOKEN_MONITOR_DATA_DIR", None)
    shutil.rmtree(_test_data_tmp, ignore_errors=True)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QFont, QFontMetrics  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# Ensure QApplication exists
_app = QApplication.instance() or QApplication(sys.argv)

from widget import (  # noqa: E402
    TokenWidget,
    MIMO_MODE, THIRD_PARTY_MODE, OVERVIEW_MODE,
    BASE_HEIGHT, OVERVIEW_HEIGHT, DUAL_BAR_GAP,
    BG_COLOR, TEXT_COLOR, _format_overview_expiry,
    _parse_reset_datetime, _format_overview_reset_time, _format_overview_reset_days,
)
from router_control import RouterResult  # noqa: E402


def _make_widget(**overrides):
    """Create a TokenWidget with safe defaults (no real network/config)."""
    cfg = {
        "display_mode": MIMO_MODE,
        "position": [200, 200],
        "always_on_top": False,
        "opacity": 1.0,
        "refresh_interval": 99999,
        "cookie": "",
        "third_party_api_key": "",
        "third_party_base_url": "http://example.com",
        "gpt_session_cookie": "",
    }
    cfg.update(overrides)
    w = TokenWidget(cfg)
    w._timer.stop()
    w._do_fetch_mimo = lambda: None
    w._do_fetch_third_party = lambda: None
    w._do_fetch_gpt = lambda: None
    return w


class _OverviewPainter:
    """Small painter double for checking overview text and pen colors."""

    def __init__(self):
        self._pen = None
        self._font = QFont()
        self.text_records = []

    def setPen(self, pen):
        self._pen = pen

    def setFont(self, font):
        self._font = QFont(font)

    def setBrush(self, _brush):
        pass

    def drawText(self, *args):
        rect = args[0] if isinstance(args[0], QRect) else None
        alignment = args[1] if rect is not None and len(args) == 3 else None
        self.text_records.append(
            (rect, args[-1], self._pen.color(), QFont(self._font), alignment)
        )

    def drawRoundedRect(self, *_args):
        pass

    def save(self):
        pass

    def restore(self):
        pass

    def setClipPath(self, _path):
        pass


class TestModeCycle(unittest.TestCase):
    """Mode cycle order: mimo -> third_party -> overview -> mimo."""

    def test_cycle_order(self):
        w = _make_widget(display_mode=MIMO_MODE)
        self.assertEqual(w.cfg["display_mode"], MIMO_MODE)
        w._toggle_display_mode()
        self.assertEqual(w.cfg["display_mode"], THIRD_PARTY_MODE)
        w._toggle_display_mode()
        self.assertEqual(w.cfg["display_mode"], OVERVIEW_MODE)
        w._toggle_display_mode()
        self.assertEqual(w.cfg["display_mode"], MIMO_MODE)
        w.close()

    def test_cycle_from_overview(self):
        w = _make_widget(display_mode=OVERVIEW_MODE)
        w._toggle_display_mode()
        self.assertEqual(w.cfg["display_mode"], MIMO_MODE)
        w.close()

    def test_switch_button_mouse_move_does_not_drag_window(self):
        w = _make_widget(display_mode=MIMO_MODE)
        w.move(200, 200)
        w._switch_rect = QRect(0, 0, 20, 20)

        press = Mock()
        press.button.return_value = Qt.MouseButton.LeftButton
        press.pos.return_value = QPoint(10, 10)
        w.mousePressEvent(press)

        move = Mock()
        move.buttons.return_value = Qt.MouseButton.LeftButton
        move.position.return_value.toPoint.return_value = QPoint(11, 10)
        w.mouseMoveEvent(move)

        self.assertEqual((w.x(), w.y()), (200, 200))
        w.close()


class TestOverviewFetchDispatch(unittest.TestCase):
    """Overview refresh dispatches both real sources without network calls in the UI thread."""

    def test_overview_starts_both_sources(self):
        w = _make_widget(display_mode=OVERVIEW_MODE, cookie="fake", third_party_api_key="k")
        calls = []
        w._do_fetch_mimo = lambda: calls.append("mimo")
        w._do_fetch_third_party = lambda: calls.append("third_party")
        w._do_fetch_gpt = lambda: calls.append("gpt")

        w._do_fetch()

        self.assertEqual(calls, ["mimo", "third_party", "gpt"])
        w.close()


    def test_refresh_attempt_updates_timestamp_before_dispatch(self):
        w = _make_widget(display_mode=OVERVIEW_MODE)
        calls = []
        w._do_fetch_mimo = lambda: calls.append(("mimo", w._last_update))
        w._do_fetch_third_party = lambda: calls.append(("third_party", w._last_update))
        w._do_fetch_gpt = lambda: calls.append(("gpt", w._last_update))

        with patch("widget.datetime") as fake_datetime:
            fake_datetime.now.return_value.strftime.return_value = "13:14:15"
            w._do_fetch()

        self.assertEqual(w._last_update, "13:14:15")
        self.assertEqual(
            calls,
            [
                ("mimo", "13:14:15"),
                ("third_party", "13:14:15"),
                ("gpt", "13:14:15"),
            ],
        )
        w.close()

    def test_configured_refresh_interval_is_applied_to_timer(self):
        w = _make_widget(refresh_interval=3600)
        self.assertEqual(w._timer.interval(), 3_600_000)
        w.close()


class TestPlaywrightRecovery(unittest.TestCase):
    def test_expired_api_cookie_requires_a_changed_playwright_cookie(self):
        w = _make_widget(cookie="stale", playwright_auto_refresh=True)
        w._refresh_playwright_cookie = Mock()

        w._on_fetch_done(
            {"ok": False, "balance": None, "error": "Cookie 已过期，请重新获取"},
            {"ok": False, "data": None, "error": "Cookie 已过期，请重新获取"},
        )

        w._refresh_playwright_cookie.assert_called_once_with(
            interactive=False,
            require_cookie_change=True,
        )
        w.close()

    def test_playwright_success_is_silent_and_failure_warns(self):
        w = _make_widget(cookie="stale")
        w._do_fetch = Mock()
        w._playwright_worker = Mock()

        with patch("widget.QMessageBox.information") as information, patch(
            "widget.QMessageBox.warning"
        ) as warning:
            w._on_playwright_cookie_done("fresh", None, False)
            information.assert_not_called()
            warning.assert_not_called()
            self.assertEqual(w.cfg["cookie"], "fresh")
            w._on_playwright_cookie_done(None, "refresh failed", False)

        warning.assert_called_once()
        w.close()


class TestHeightSwitch(unittest.TestCase):
    """Overview uses the same compact height as the other display modes."""

    def test_base_height(self):
        w = _make_widget(display_mode=MIMO_MODE)
        self.assertEqual(w.height(), BASE_HEIGHT)
        w.close()

    def test_overview_height(self):
        w = _make_widget(display_mode=OVERVIEW_MODE)
        self.assertEqual(w.height(), BASE_HEIGHT)
        self.assertEqual(OVERVIEW_HEIGHT, BASE_HEIGHT)
        w.close()

    def test_switch_restores_height(self):
        w = _make_widget(display_mode=OVERVIEW_MODE)
        self.assertEqual(w.height(), OVERVIEW_HEIGHT)
        w._set_display_mode(MIMO_MODE)
        self.assertEqual(w.height(), BASE_HEIGHT)
        w.close()


class TestOverviewPercentFormat(unittest.TestCase):
    """Static formatting for overview percentage labels."""

    def test_not_configured(self):
        self.assertEqual(TokenWidget._format_overview_percent(None, False), "\u672a\u914d\u7f6e")

    def test_no_data(self):
        self.assertEqual(TokenWidget._format_overview_percent(None, True), "--")

    def test_zero(self):
        self.assertEqual(TokenWidget._format_overview_percent(0.0, True), "0.0%")

    def test_mid(self):
        self.assertEqual(TokenWidget._format_overview_percent(42.5, True), "42.5%")

    def test_clamp_above(self):
        self.assertEqual(TokenWidget._format_overview_percent(150.0, True), "100.0%")

    def test_clamp_below(self):
        self.assertEqual(TokenWidget._format_overview_percent(-10.0, True), "0.0%")


class TestOverviewRowMetrics(unittest.TestCase):
    """Overview rows stack vertically and fit inside the compact window."""

    def test_three_rows_stack_without_overlap(self):
        rows = [TokenWidget._overview_row_metrics(index) for index in range(3)]
        self.assertEqual([row[0] for row in rows], [16, 16, 16])
        for previous, current in zip(rows, rows[1:]):
            previous_bar_bottom = previous[2] + previous[4]
            current_label_top = current[1] - 14
            self.assertLessEqual(previous_bar_bottom, current_label_top)
        self.assertLessEqual(rows[-1][2] + rows[-1][4] + 10, BASE_HEIGHT)

    def test_bars_match_token_plan_size(self):
        for index in range(3):
            _, _, _, bar_width, bar_height = TokenWidget._overview_row_metrics(index)
            self.assertEqual((bar_width, bar_height), (228, 14))


class TestOverviewExpiryRendering(unittest.TestCase):
    def _render_first_row(self, days_left=2, alert_enabled=True, row_width=500):
        expiry_date = (datetime.now().date() + timedelta(days=days_left)).strftime("%Y-%m-%d")
        w = _make_widget(
            display_mode=OVERVIEW_MODE,
            cookie="fake",
            expiry_date=expiry_date,
            expiry_alert_enabled=alert_enabled,
        )
        w._plan_total = 1000
        w._plan_used = 425
        painter = _OverviewPainter()
        def row_metrics(index):
            return 16, 38 + index * 34, 44 + index * 34, row_width if index == 0 else 228, 14

        with patch.object(TokenWidget, "_overview_row_metrics", side_effect=row_metrics):
            w._paint_overview(painter)
        return w, expiry_date, painter

    @staticmethod
    def _first_row_records(painter):
        return [record for record in painter.text_records if record[0] and record[0].y() == 24]

    def test_first_row_combines_title_and_short_expiry(self):
        w, expiry_date, painter = self._render_first_row()
        expiry_text = _format_overview_expiry(expiry_date)
        records = self._first_row_records(painter)
        title_records = [record for record in records if record[1] != "42.5%"]

        self.assertEqual(expiry_text, "2天")
        self.assertEqual(
            "".join(record[1] for record in title_records),
            f"Token Plan {expiry_text}",
        )
        self.assertEqual(title_records[0][2], TEXT_COLOR)
        w.close()

    def test_first_row_date_and_reminder_keep_title_color(self):
        for alert_enabled in (True, False):
            with self.subTest(alert_enabled=alert_enabled):
                w, expiry_date, painter = self._render_first_row(
                    days_left=2, alert_enabled=alert_enabled
                )
                title_records = [
                    record
                    for record in self._first_row_records(painter)
                    if record[1] != "42.5%"
                ]
                self.assertEqual(title_records[0][2], TEXT_COLOR)
                w.close()

    def test_first_row_accepts_padded_and_unpadded_dates(self):
        for expiry_date in ("2099-09-25", "2099-9-25"):
            days_left = max(0, (datetime.strptime(expiry_date, "%Y-%m-%d").date() - datetime.now().date()).days)
            self.assertEqual(_format_overview_expiry(expiry_date), f"{days_left}天")

    def test_first_row_invalid_or_missing_expiry_has_no_suffix(self):
        self.assertEqual(_format_overview_expiry(""), "")
        self.assertEqual(_format_overview_expiry("not-a-date"), "")
        today = datetime.now().date()
        self.assertEqual(_format_overview_expiry(today.isoformat()), "0天")
        self.assertEqual(
            _format_overview_expiry((today - timedelta(days=1)).isoformat()), "0天"
        )

    def test_first_row_text_uses_nine_point_font_and_leaves_percent_room(self):
        w, expiry_date, painter = self._render_first_row(row_width=228)
        records = self._first_row_records(painter)
        percent_record = next(record for record in records if record[1] == "42.5%")
        percent_start = (
            percent_record[0].right()
            + 1
            - QFontMetrics(percent_record[3]).horizontalAdvance("42.5%")
        )

        self.assertTrue(records)
        self.assertTrue(all(record[3].family() == "Microsoft YaHei" for record in records))
        self.assertTrue(all(record[3].pointSize() == 9 for record in records))
        self.assertLess(max(record[0].right() for record in records if record[1] != "42.5%"), percent_start)
        w.close()


class TestMimoTooltipLines(unittest.TestCase):
    """MiMo tooltip builder returns plan percentage when data exists."""

    def test_with_plan_data(self):
        w = _make_widget(cookie="fake")
        w._plan_total = 1000
        w._plan_used = 250
        lines = w._build_mimo_tooltip_lines()
        joined = "\n".join(lines)
        self.assertIn("Token Plan: 25.0%", joined)
        w.close()

    def test_no_plan(self):
        w = _make_widget(cookie="fake")
        w._plan_total = 0
        lines = w._build_mimo_tooltip_lines()
        self.assertNotIn("Token Plan", "\n".join(lines))
        w.close()


class TestWlbResetRendering(unittest.TestCase):
    def test_iso_reset_time_is_converted_to_local_time(self):
        utc_reset = datetime(2099, 9, 1, 12, 34, 56, tzinfo=timezone.utc)

        self.assertEqual(
            _parse_reset_datetime(utc_reset.isoformat().replace("+00:00", "Z")),
            utc_reset.astimezone().replace(tzinfo=None),
        )

    def test_unix_reset_time_and_relative_reset_fallback(self):
        utc_reset = datetime(2099, 9, 1, 12, 34, 56, tzinfo=timezone.utc)
        timestamp = utc_reset.timestamp()

        self.assertEqual(
            _parse_reset_datetime(timestamp),
            utc_reset.astimezone().replace(tzinfo=None),
        )
        self.assertEqual(
            _format_overview_reset_time(timestamp),
            utc_reset.astimezone().strftime("%H:%M"),
        )
        self.assertEqual(
            _format_overview_reset_days(timestamp),
            f"{(utc_reset.astimezone().date() - datetime.now().date()).days}天",
        )

        before = datetime.now().astimezone().replace(tzinfo=None)
        fallback = _parse_reset_datetime(None, 3600)
        self.assertAlmostEqual((fallback - before).total_seconds(), 3600, delta=1)
        self.assertRegex(_format_overview_reset_time(None, 3600), r"^\d{2}:\d{2}$")
        self.assertRegex(_format_overview_reset_days(None, 5 * 86400), r"^\d+天$")

    def test_overview_wlb_dual_labels_are_compact_and_use_nine_point_font(self):
        reset_at = (
            datetime.now().astimezone() + timedelta(days=5)
        ).replace(microsecond=0).isoformat()
        w = _make_widget(display_mode=OVERVIEW_MODE, third_party_api_key="k")
        w._tp_data = {
            "daily": {"used_percent": 12.5, "reset_at": reset_at},
            "used_percent": 42.5,
            "remaining_percent": 57.5,
            "window": "7d",
            "total_percent": 100,
            "reset_at": reset_at,
        }
        painter = _OverviewPainter()

        w._paint_overview(painter)

        row_records = [
            record for record in painter.text_records if record[0] and record[0].y() == 58
        ]
        texts = [record[1] for record in row_records]
        self.assertEqual(
            texts,
            [f"WLB - {_format_overview_reset_time(reset_at)}", "12.5%", "5天", "42.5%"],
        )
        self.assertTrue(all(record[3].pointSize() == 9 for record in row_records))
        self.assertFalse(any("重置" in text or "还剩" in text for text in texts))
        self.assertTrue(all(record[2] == TEXT_COLOR for record in row_records))
        w.close()

    def test_overview_wlb_missing_daily_window_shows_no_data(self):
        w = _make_widget(display_mode=OVERVIEW_MODE, third_party_api_key="k")
        w._tp_data = {
            "daily": {"has_rate_limit": False, "used_percent": 0},
            "used_percent": 42.5,
            "remaining_percent": 57.5,
            "window": "7d",
            "total_percent": 100,
        }
        painter = _OverviewPainter()

        w._paint_overview(painter)

        row_records = [
            record for record in painter.text_records if record[0] and record[0].y() == 58
        ]
        texts = [record[1] for record in row_records]
        self.assertEqual(texts, ["WLB - --:--", "--", "--天", "42.5%"])
        self.assertTrue(all(record[3].pointSize() == 9 for record in row_records))
        w.close()

    def test_overview_gpt_dual_labels_use_primary_time_and_weekly_days(self):
        primary_reset = datetime.now().astimezone() + timedelta(hours=1, minutes=46)
        w = _make_widget(display_mode=OVERVIEW_MODE, gpt_session_cookie="k")
        w._gpt_data = {
            "used_percent": 42.5,
            "primary": {"used_percent": 12.5, "reset_at": primary_reset.timestamp()},
            "secondary": {"used_percent": 42.5, "reset_after": 5 * 86400},
        }
        painter = _OverviewPainter()

        w._paint_overview(painter)

        row_records = [
            record for record in painter.text_records if record[0] and record[0].y() == 92
        ]
        texts = [record[1] for record in row_records]
        self.assertEqual(
            texts,
            [
                f"GPT - {_format_overview_reset_time(primary_reset.timestamp())}",
                "12.5%",
                _format_overview_reset_days(None, 5 * 86400),
                "42.5%",
            ],
        )
        segment_width = (228 - DUAL_BAR_GAP) // 2
        left_records = row_records[:2]
        self.assertEqual(left_records[0][0].x(), 16)
        self.assertEqual(left_records[1][0], QRect(16, 92, segment_width, 18))
        self.assertEqual(
            [record[4] for record in left_records],
            [
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            ],
        )
        self.assertTrue(all(record[3].pointSize() == 9 for record in row_records))
        w.close()

    def test_overview_gpt_missing_reset_uses_placeholders(self):
        w = _make_widget(display_mode=OVERVIEW_MODE, gpt_session_cookie="k")
        w._gpt_data = {
            "used_percent": 42.5,
            "primary": {"used_percent": 12.5},
            "secondary": {"used_percent": 42.5},
        }
        painter = _OverviewPainter()

        w._paint_overview(painter)

        row_records = [
            record for record in painter.text_records if record[0] and record[0].y() == 92
        ]
        self.assertEqual(
            [record[1] for record in row_records],
            ["GPT - --:--", "12.5%", "--天", "42.5%"],
        )
        w.close()


class TestThirdPartyTooltipLines(unittest.TestCase):
    """Third-party tooltip builder shows percentages from _tp_data."""

    def test_with_data(self):
        w = _make_widget(third_party_api_key="k")
        w._tp_data = {"used_percent": 33.33, "remaining_percent": 66.67,
                       "is_valid": True, "window": "7d", "total_percent": 100,
                       "reset_at": "2099-09-01T12:34:56Z"}
        lines = w._build_third_party_tooltip_lines()
        joined = "\n".join(lines)
        self.assertIn("33.33%", joined)
        self.assertIn("Active", joined)
        expected = datetime(2099, 9, 1, 12, 34, 56, tzinfo=timezone.utc)
        self.assertIn(f"7日重置: {expected.astimezone():%Y-%m-%d %H:%M:%S}", joined)
        w.close()

    def test_no_data(self):
        w = _make_widget(third_party_api_key="k")
        w._tp_data = None
        lines = w._build_third_party_tooltip_lines()
        self.assertEqual(lines[0], "WLB")
        self.assertEqual(len(lines), 1)
        w.close()


class TestOverviewTooltipLines(unittest.TestCase):
    """Overview tooltip lists both sources with correct labels."""

    def test_no_config(self):
        w = _make_widget()
        lines = w._build_overview_tooltip_lines()
        joined = "\n".join(lines)
        self.assertIn("Token Plan: \u672a\u914d\u7f6e", joined)
        self.assertIn("WLB \u65e5\u9650\u989d: \u672a\u914d\u7f6e", joined)
        self.assertIn("WLB \u5468\u9650\u989d: \u672a\u914d\u7f6e", joined)
        w.close()

    def test_mimo_configured_no_data(self):
        w = _make_widget(cookie="fake")
        w._plan_total = 0
        lines = w._build_overview_tooltip_lines()
        joined = "\n".join(lines)
        self.assertIn("Token Plan: --", joined)
        w.close()

    def test_mimo_with_data(self):
        w = _make_widget(cookie="fake")
        w._plan_total = 200
        w._plan_used = 50
        lines = w._build_overview_tooltip_lines()
        joined = "\n".join(lines)
        self.assertIn("Token Plan: 25.0%", joined)
        w.close()

    def test_tp_with_data(self):
        w = _make_widget(third_party_api_key="k")
        w._tp_data = {"daily": {"used_percent": 6.25},
                       "used_percent": 12.5, "remaining_percent": 87.5,
                       "is_valid": True, "window": "7d", "total_percent": 100}
        lines = w._build_overview_tooltip_lines()
        joined = "\n".join(lines)
        self.assertIn("WLB \u65e5\u9650\u989d: 6.2%", joined)
        self.assertIn("WLB \u5468\u9650\u989d: 12.5%", joined)
        w.close()

    def test_error_included_via_apply(self):
        """Errors are appended by _apply_overview_tooltip_if_needed, not the builder."""
        w = _make_widget(display_mode=OVERVIEW_MODE, cookie="fake")
        w._mimo_error = "test error"
        w._tp_error = "api error"
        w._refresh_error_state()
        w._apply_overview_tooltip_if_needed()
        tooltip = w.toolTip()
        self.assertIn("test error", tooltip)
        self.assertIn("api error", tooltip)
        self.assertIn("总览", tooltip)
        w.close()


class TestThirdPartyCallbackExtraction(unittest.TestCase):
    """Verify _on_third_party_fetch_done extracts flat parsed dict from API wrapper.

    api_client.fetch_third_party_usage() returns:
        {"ok": True, "data": parsed_dict, "error": None, "url": ...}
    where parsed_dict contains used_percent, remaining_percent, is_valid, etc.
    _tp_data must be the flat parsed_dict, not the wrapper.
    """

    def test_callback_extracts_data_from_ok_wrapper(self):
        w = _make_widget(display_mode=THIRD_PARTY_MODE, third_party_api_key="k")
        parsed = {"used_percent": 42.0, "remaining_percent": 58.0,
                  "is_valid": True, "window": "7d", "total_percent": 100}
        wrapper = {"ok": True, "data": parsed, "error": None, "url": "http://example.com"}
        w._on_third_party_fetch_done(wrapper)
        # _tp_data must be the flat parsed dict
        self.assertIs(w._tp_data, parsed)
        self.assertEqual(w._tp_data["used_percent"], 42.0)
        self.assertEqual(w._last_error, "")
        w.close()

    def test_callback_sets_error_on_failure(self):
        w = _make_widget(display_mode=THIRD_PARTY_MODE, third_party_api_key="k")
        wrapper = {"ok": False, "data": None, "error": "API Key 无效"}
        w._on_third_party_fetch_done(wrapper)
        self.assertIsNone(w._tp_data)
        self.assertIn("无效", w._last_error)
        w.close()

    def test_callback_does_not_clear_data_on_failure(self):
        w = _make_widget(display_mode=THIRD_PARTY_MODE, third_party_api_key="k")
        w._tp_data = {"used_percent": 10.0, "remaining_percent": 90.0,
                       "is_valid": True, "window": "7d", "total_percent": 100}
        wrapper = {"ok": False, "data": None, "error": "timeout"}
        w._on_third_party_fetch_done(wrapper)
        # Old data should be preserved on failure
        self.assertIsNotNone(w._tp_data)
        self.assertEqual(w._tp_data["used_percent"], 10.0)
        w.close()

    def test_callback_tooltip_reflects_extracted_data(self):
        w = _make_widget(display_mode=OVERVIEW_MODE, third_party_api_key="k")
        parsed = {"used_percent": 55.5, "remaining_percent": 44.5,
                  "is_valid": True, "window": "7d", "total_percent": 100}
        wrapper = {"ok": True, "data": parsed, "error": None, "url": "http://example.com"}
        w._on_third_party_fetch_done(wrapper)
        tooltip = w.toolTip()
        self.assertIn("55.5%", tooltip)
        w.close()


class TestGPTCallback(unittest.TestCase):
    def test_overview_tooltip_shows_both_gpt_windows(self):
        w = _make_widget(display_mode=OVERVIEW_MODE)
        w._gpt_data = {
            "used_percent": 40.0,
            "primary": {"used_percent": 20.0},
            "secondary": {"used_percent": 40.0},
        }

        lines = w._build_overview_tooltip_lines()

        self.assertIn("GPT 5\u5c0f\u65f6: 20.0%", lines)
        self.assertIn("GPT \u5468\u9650\u989d: 40.0%", lines)
        w.close()

    def test_failure_keeps_last_data_and_marks_it_stale(self):
        w = _make_widget(display_mode=OVERVIEW_MODE)
        old_data = {"used_percent": 30.0, "remaining_percent": 70.0}
        w._gpt_data = old_data

        w._on_gpt_fetch_done({"ok": False, "data": None, "error": "请求超时"})

        self.assertIs(w._gpt_data, old_data)
        self.assertIn("继续显示上次数据", w._gpt_error)
        self.assertIn("请求超时", w.toolTip())
        w.close()


class TestOverviewDenominatorZero(unittest.TestCase):
    """_plan_used/_plan_total denominator-zero produces -- and empty bar."""

    def test_zero_denominator(self):
        w = _make_widget(cookie="fake")
        w._plan_total = 0
        w._plan_used = 0
        has_cookie = True
        mimo_pct = (w._plan_used / w._plan_total * 100) if has_cookie and w._plan_total > 0 else None
        self.assertIsNone(mimo_pct)
        formatted = TokenWidget._format_overview_percent(mimo_pct, has_cookie)
        self.assertEqual(formatted, "--")
        w.close()


class TestOverviewRenderSmoke(unittest.TestCase):
    """Smoke-test: render overview mode to QImage without clipping."""

    def test_overview_render_no_clip(self):
        from PyQt6.QtGui import QImage
        w = _make_widget(display_mode=OVERVIEW_MODE, cookie="fake", third_party_api_key="k", gpt_session_cookie="fake_gpt")
        w._plan_total = 1000
        w._plan_used = 300
        w._tp_data = {"daily": {"used_percent": 35.0},
                       "used_percent": 55.0, "remaining_percent": 45.0,
                       "is_valid": True, "window": "7d", "total_percent": 100}
        w._gpt_data = {"used_percent": 30.0, "remaining_percent": 70.0,
                       "primary": {"used_percent": 20.0},
                       "secondary": {"used_percent": 30.0},
                       "reset_at": "2026-01-01T00:00:00Z", "source": "test"}
        w._timer.stop()
        w._do_fetch = lambda: None
        w._do_fetch = lambda: None
        w._last_update = "12:00:00"
        img = QImage(w.width(), w.height(), QImage.Format.Format_ARGB32)
        img.fill(0)
        w.show()
        _app.processEvents()
        w.render(img)

        x0, _, bar_y0, bar_w0, bar_h0 = TokenWidget._overview_row_metrics(0)
        x1, _, bar_y1, bar_w1, bar_h1 = TokenWidget._overview_row_metrics(1)
        x2, _, bar_y2, bar_w2, bar_h2 = TokenWidget._overview_row_metrics(2)
        bottom = bar_y2 + bar_h2 + 12
        self.assertLessEqual(bottom, w.height())
        self.assertEqual(x0, x1)
        self.assertEqual(x1, x2)
        self.assertLess(bar_y0, bar_y1)
        self.assertLess(bar_y1, bar_y2)
        self.assertGreater(img.pixelColor(x0 + bar_w0 // 2, bar_y0 + bar_h0 // 2).alpha(), 0)
        self.assertGreater(img.pixelColor(x1 + bar_w1 // 2, bar_y1 + bar_h1 // 2).alpha(), 0)
        self.assertGreater(img.pixelColor(x2 + bar_w2 // 2, bar_y2 + bar_h2 // 2).alpha(), 0)
        for x, bar_y, bar_w, bar_h in (
            (x1, bar_y1, bar_w1, bar_h1),
            (x2, bar_y2, bar_w2, bar_h2),
        ):
            segment_width = (bar_w - DUAL_BAR_GAP) // 2
            gap_color = img.pixelColor(
                x + segment_width + DUAL_BAR_GAP // 2,
                bar_y + bar_h // 2,
            )
            self.assertEqual(gap_color.getRgb(), BG_COLOR.getRgb())
        w.close()

    def test_update_time_is_painted_when_overview_has_error(self):
        w = _make_widget(display_mode=OVERVIEW_MODE)
        w._last_error = "WLB 请求失败"
        w._last_update = "12:00:00"
        painter = Mock()
        metrics = Mock()
        metrics.horizontalAdvance.return_value = 108
        metrics.elidedText.return_value = "WLB 请求失败"
        painter.fontMetrics.return_value = metrics

        w._paint_refresh_status(painter, 136)

        painted_texts = [call.args[-1] for call in painter.drawText.call_args_list]
        self.assertIn("WLB 请求失败", painted_texts)
        self.assertIn("更新于 12:00:00", painted_texts)
        w.close()


class TestRouterStatusDisplay(unittest.TestCase):
    def test_router_operation_start_status_labels(self):
        expected = {
            "refresh": "正在更新模型元数据",
            "enable": "正在开启路由",
            "disable": "正在关闭路由",
            "restart": "正在重启路由器",
        }
        for operation, label in expected.items():
            with self.subTest(operation=operation):
                w = _make_widget()
                w._router_worker = None
                worker = Mock()
                worker.isRunning.return_value = False
                w._tray_icon.showMessage = Mock()
                with patch("widget.RouterWorker", return_value=worker):
                    w._start_router_operation(operation)
                self.assertEqual(w._router_status, label)
                worker.start.assert_called_once()
                w.close()

    def test_router_status_uses_short_summary_and_refreshes_paint(self):
        w = _make_widget()
        w.update = Mock()
        w._router_worker = Mock()
        w._on_router_operation_done(
            RouterResult(False, "关闭路由失败", "敏感的长错误详情" * 50)
        )

        self.assertEqual(w._router_status, "关闭路由失败")
        self.assertNotIn("敏感的长错误详情", w._router_status)
        self.assertFalse(w._router_status_ok)
        w.update.assert_called_once()
        self.assertTrue(w._router_status_timer.isActive())
        w._clear_router_status()
        self.assertEqual(w._router_status, "")
        self.assertIsNone(w._router_status_ok)
        w.close()


if __name__ == "__main__":
    unittest.main()
