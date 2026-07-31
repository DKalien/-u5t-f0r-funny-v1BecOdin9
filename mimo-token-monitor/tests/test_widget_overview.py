# encoding: utf-8
import os, sys, tempfile, unittest, pathlib

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

from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists
_app = QApplication.instance() or QApplication(sys.argv)

from widget import (
    TokenWidget,
    MIMO_MODE, THIRD_PARTY_MODE, OVERVIEW_MODE,
    BASE_HEIGHT, OVERVIEW_HEIGHT,
    _bar_color, _fmt_tokens,
)


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


class TestThirdPartyTooltipLines(unittest.TestCase):
    """Third-party tooltip builder shows percentages from _tp_data."""

    def test_with_data(self):
        w = _make_widget(third_party_api_key="k")
        w._tp_data = {"used_percent": 33.33, "remaining_percent": 66.67,
                       "is_valid": True, "window": "7d", "total_percent": 100}
        lines = w._build_third_party_tooltip_lines()
        joined = "\n".join(lines)
        self.assertIn("33.33%", joined)
        self.assertIn("Active", joined)
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
        self.assertIn("WLB: \u672a\u914d\u7f6e", joined)
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
        w._tp_data = {"used_percent": 12.5, "remaining_percent": 87.5,
                       "is_valid": True, "window": "7d", "total_percent": 100}
        lines = w._build_overview_tooltip_lines()
        joined = "\n".join(lines)
        self.assertIn("WLB: 12.5%", joined)
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
        w._tp_data = {"used_percent": 55.0, "remaining_percent": 45.0,
                       "is_valid": True, "window": "7d", "total_percent": 100}
        w._gpt_data = {"used_percent": 30.0, "remaining_percent": 70.0,
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
        w.close()


if __name__ == "__main__":
    unittest.main()
