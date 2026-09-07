import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router_control import (
    ROUTER_ROOT_ENV,
    resolve_router_root,
    run_router_operation,
)


def make_router_root(base: Path) -> Path:
    root = base / "codex-router"
    (root / "src").mkdir(parents=True)
    (root / "codex-router.ps1").touch()
    (root / "src" / "catalog.mjs").touch()
    (root / "src" / "config-manager.mjs").touch()
    (root / "src" / "gpt-route.mjs").touch()
    (root / "src" / "service.mjs").touch()
    (root / "src" / "status.mjs").touch()
    return root


class TestRouterControl(unittest.TestCase):
    def test_resolve_root_from_install_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = make_router_root(base)
            codex_home = base / "home" / ".codex"
            manifest = codex_home / "codex-router" / "install-manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps({"current": {"sourceRoot": str(root)}}),
                encoding="utf-8",
            )

            self.assertEqual(
                resolve_router_root({"CODEX_HOME": str(codex_home)}),
                root.resolve(),
            )

    def test_refresh_runs_catalog_then_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, b"ok", b"")

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "refresh",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertTrue(result.ok)
            self.assertEqual(
                [call[0][1:] for call in calls],
                [
                    [str(root / "src" / "catalog.mjs")],
                    [str(root / "src" / "service.mjs"), "restart"],
                ],
            )
            self.assertTrue(all(call[1]["cwd"] == root.resolve() for call in calls))
            self.assertTrue(
                all(call[1]["stdin"] == subprocess.DEVNULL for call in calls)
            )

    def test_failure_stops_before_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))
            calls = []

            def runner(command, **_kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 1, b"", "目录生成失败".encode())

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "refresh",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertFalse(result.ok)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result.detail, "目录生成失败")

    def test_route_controls_reuse_existing_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))
            calls = []

            def runner(command, **_kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                for operation in ("enable", "disable", "restart"):
                    result = run_router_operation(
                        operation,
                        runner=runner,
                        environ={ROUTER_ROOT_ENV: str(root)},
                    )
                    self.assertTrue(result.ok)

            self.assertEqual(calls[0][-2:], [str(root / "codex-router.ps1"), "enable"])
            self.assertEqual(calls[1][-2:], [str(root / "codex-router.ps1"), "disable"])
            self.assertEqual(calls[2][-2:], [str(root / "src" / "service.mjs"), "restart"])

    def test_status_uses_router_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))
            calls = []

            def runner(command, **_kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "config": {"mode": "router", "managed": True},
                            "health": {"ok": True},
                            "gptRoute": {
                                "mode": "wlb",
                                "readable": True,
                                "ready": True,
                            },
                        }
                    ).encode(),
                    b"",
                )

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "status",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertTrue(result.ok)
            self.assertTrue(result.route_enabled)
            self.assertEqual(result.gpt_route, "wlb")
            self.assertEqual(
                calls,
                [["node", str(root / "src" / "status.mjs"), "--json"]],
            )

    def test_status_exit_one_keeps_native_official_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(
                    command,
                    1,
                    json.dumps(
                        {
                            "config": {"mode": "native", "managed": False},
                            "health": {"ok": False},
                            "gptRoute": {
                                "mode": "official",
                                "readable": True,
                                "ready": True,
                            },
                        }
                    ).encode(),
                    b"",
                )

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "status",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertTrue(result.ok)
            self.assertFalse(result.route_enabled)
            self.assertEqual(result.gpt_route, "official")
            self.assertEqual(result.detail, "路由器尚未就绪")

    def test_status_unhealthy_router_does_not_claim_gpt_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(
                    command,
                    1,
                    json.dumps(
                        {
                            "config": {"mode": "router", "managed": True},
                            "health": {"ok": False},
                            "gptRoute": {
                                "mode": "wlb",
                                "readable": True,
                                "ready": True,
                            },
                        }
                    ).encode(),
                    b"",
                )

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "status",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertTrue(result.ok)
            self.assertTrue(result.route_enabled)
            self.assertIsNone(result.gpt_route)

    def test_status_exit_two_is_execution_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(command, 2, b"", "参数错误".encode())

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "status",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.detail, "参数错误")
            self.assertIsNone(result.gpt_route)

    def test_status_malformed_json_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))

            def runner(command, **_kwargs):
                return subprocess.CompletedProcess(command, 1, b"{broken", b"")

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "status",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.detail, "路由器返回了无效状态")
            self.assertIsNone(result.gpt_route)

    def test_route_switch_checks_enabled_and_reports_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                output = (
                    json.dumps(
                        {
                            "config": {"mode": "router", "managed": True},
                            "health": {"ok": True},
                            "gptRoute": {
                                "mode": "unknown",
                                "readable": False,
                                "ready": False,
                            },
                        }
                    ).encode()
                    if len(calls) == 1
                    else b""
                )
                return subprocess.CompletedProcess(
                    command, 1 if len(calls) == 1 else 0, output, b""
                )

            environ = {ROUTER_ROOT_ENV: str(root), "CODEX_HOME": str(Path(tmp) / "home")}
            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "route-wlb", runner=runner, environ=environ
                )

            self.assertTrue(result.ok)
            self.assertTrue(result.route_enabled)
            self.assertEqual(result.gpt_route, "wlb")
            self.assertEqual(
                [call[0][1:] for call in calls],
                [
                    [str(root / "src" / "status.mjs"), "--json"],
                    [str(root / "src" / "gpt-route.mjs"), "wlb"],
                ],
            )
            self.assertTrue(all(call[1]["env"] == environ for call in calls))

    def test_route_switch_refuses_when_router_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(
                    command,
                    1,
                    json.dumps(
                        {
                            "config": {"mode": "native", "managed": False},
                            "health": {"ok": False},
                            "gptRoute": {
                                "mode": "official",
                                "readable": True,
                                "ready": True,
                            },
                        }
                    ).encode(),
                    b"",
                )

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "route-official",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertFalse(result.ok)
            self.assertIn("先开启路由", result.detail)
            self.assertIsNone(result.route_enabled)
            self.assertIsNone(result.gpt_route)
            self.assertEqual(len(calls), 1)

    def test_route_switch_failure_keeps_route_fields_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_router_root(Path(tmp))
            calls = []

            def runner(command, **_kwargs):
                calls.append(command)
                if len(calls) == 1:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps(
                            {
                                "config": {"mode": "router", "managed": True},
                                "health": {"ok": True},
                                "gptRoute": {
                                    "mode": "wlb",
                                    "readable": True,
                                    "ready": True,
                                },
                            }
                        ).encode(),
                        b"",
                    )
                return subprocess.CompletedProcess(command, 1, b"", "切换失败".encode())

            with patch("router_control.hidden_subprocess_kwargs", return_value={}):
                result = run_router_operation(
                    "route-official",
                    runner=runner,
                    environ={ROUTER_ROOT_ENV: str(root)},
                )

            self.assertFalse(result.ok)
            self.assertEqual(result.detail, "切换失败")
            self.assertIsNone(result.route_enabled)
            self.assertIsNone(result.gpt_route)


if __name__ == "__main__":
    unittest.main()
