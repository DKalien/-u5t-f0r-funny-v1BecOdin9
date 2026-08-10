"""Run Codex Router maintenance commands for the tray menu."""

from __future__ import annotations

import json
import locale
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from process_utils import hidden_subprocess_kwargs


ROUTER_ROOT_ENV = "MIMO_TOKEN_MONITOR_ROUTER_ROOT"


@dataclass(frozen=True)
class RouterResult:
    ok: bool
    message: str
    detail: str = ""
    route_enabled: bool | None = None


def resolve_router_root(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = str(env.get(ROUTER_ROOT_ENV, "")).strip()
    if configured:
        root = Path(os.path.expandvars(configured)).expanduser()
    else:
        codex_home = Path(env.get("CODEX_HOME", Path.home() / ".codex"))
        manifest_path = codex_home / "codex-router" / "install-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            root = Path(manifest["current"]["sourceRoot"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"找不到 Codex Router 安装位置，请设置 {ROUTER_ROOT_ENV}。"
            ) from exc

    root = root.resolve()
    required = (
        root / "codex-router.ps1",
        root / "src" / "catalog.mjs",
        root / "src" / "config-manager.mjs",
        root / "src" / "service.mjs",
    )
    if not all(path.is_file() for path in required):
        raise RuntimeError(f"Codex Router 目录无效：{root}")
    return root


def _commands(operation: str, root: Path) -> list[list[str]]:
    node = "node"
    catalog = str(root / "src" / "catalog.mjs")
    service = str(root / "src" / "service.mjs")
    config_manager = str(root / "src" / "config-manager.mjs")
    script = str(root / "codex-router.ps1")
    if operation == "status":
        return [[node, config_manager, "status"]]
    if operation == "refresh":
        return [[node, catalog], [node, service, "restart"]]
    if operation == "restart":
        return [[node, service, "restart"]]
    if operation in {"enable", "disable"}:
        return [[
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script,
            operation,
        ]]
    raise ValueError(f"未知路由操作：{operation}")


def _decode(value) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        return ""
    encodings = ("utf-8", locale.getpreferredencoding(False))
    for encoding in dict.fromkeys(encodings):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace")


def _failure_detail(result: subprocess.CompletedProcess) -> str:
    output = _decode(result.stderr).strip() or _decode(result.stdout).strip()
    return (output.splitlines()[-1] if output else f"命令退出码 {result.returncode}")[:500]


def run_router_operation(
    operation: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    environ: Mapping[str, str] | None = None,
    timeout_seconds: float = 360,
) -> RouterResult:
    labels = {
        "status": "检查路由状态",
        "refresh": "更新模型元数据",
        "enable": "开启路由",
        "disable": "关闭路由",
        "restart": "重启路由器",
    }
    label = labels.get(operation, "路由操作")
    try:
        root = resolve_router_root(environ)
        commands = _commands(operation, root)
    except (RuntimeError, ValueError) as exc:
        return RouterResult(False, f"{label}失败", str(exc))

    for command in commands:
        try:
            result = runner(
                command,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return RouterResult(False, f"{label}失败", "操作超时")
        except OSError as exc:
            return RouterResult(False, f"{label}失败", str(exc))
        if result.returncode != 0:
            return RouterResult(False, f"{label}失败", _failure_detail(result))

    if operation == "status":
        try:
            mode = json.loads(_decode(result.stdout))["mode"]
            if mode not in {"router", "native"}:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return RouterResult(False, f"{label}失败", "路由器返回了无效状态")
        enabled = mode == "router"
        return RouterResult(
            True,
            "路由已开启" if enabled else "路由已关闭",
            route_enabled=enabled,
        )

    messages = {
        "refresh": "模型元数据已更新，路由器已重启",
        "enable": "路由已开启",
        "disable": "路由已关闭",
        "restart": "路由器已重启",
    }
    return RouterResult(
        True,
        messages[operation],
        route_enabled={"enable": True, "disable": False}.get(operation),
    )
