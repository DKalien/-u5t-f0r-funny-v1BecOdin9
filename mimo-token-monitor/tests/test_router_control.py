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
    (root / "src" / "service.mjs").touch()
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


if __name__ == "__main__":
    unittest.main()
