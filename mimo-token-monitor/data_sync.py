from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path, PurePosixPath

from config import _db_path, _project_data_dir

_ALLOWED_GIT_PATH = "mimo-token-monitor/settings.db"


class SyncStatus(str, Enum):
    SUCCESS = "success"
    NO_CHANGE = "no_change"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class SyncResult:
    status: SyncStatus
    stage: str
    message: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {SyncStatus.SUCCESS, SyncStatus.NO_CHANGE}


@dataclass(frozen=True)
class SyncConfig:
    repo_root: Path
    data_dir: Path
    db_path: Path
    git_path: str = _ALLOWED_GIT_PATH
    remote: str = "origin"
    branch: str = "main"
    timeout_seconds: int = 30
    push_retries: int = 3

    @classmethod
    def from_environment(cls) -> "SyncConfig":
        data_dir = Path(_project_data_dir()).resolve()
        timeout = _positive_int("MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS", 30)
        retries = _positive_int("MIMO_TOKEN_MONITOR_GIT_PUSH_RETRIES", 3)
        return cls(
            repo_root=data_dir.parent.resolve(),
            data_dir=data_dir,
            db_path=Path(_db_path()).resolve(),
            remote=_nonempty_env("MIMO_TOKEN_MONITOR_GIT_REMOTE", "origin"),
            branch=_nonempty_env("MIMO_TOKEN_MONITOR_GIT_BRANCH", "main"),
            timeout_seconds=timeout,
            push_retries=retries,
        )


def _nonempty_env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    if not value:
        raise ValueError(f"{name} 不能为空")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


class DataSyncService:
    def __init__(self, config: SyncConfig):
        self.config = config

    def validate_paths(self) -> SyncResult | None:
        cfg = self.config
        if cfg.git_path != _ALLOWED_GIT_PATH or PurePosixPath(cfg.git_path).is_absolute():
            return SyncResult(SyncStatus.SKIPPED, "validate", "同步目标路径不受允许")

        repo_root = cfg.repo_root.resolve()
        data_dir = cfg.data_dir.resolve()
        db_path = cfg.db_path.resolve()
        if db_path.parent != data_dir:
            return SyncResult(SyncStatus.SKIPPED, "validate", "数据库不在指定数据目录")
        if data_dir.parent != repo_root:
            return SyncResult(SyncStatus.SKIPPED, "validate", "数据目录与仓库根目录不匹配")
        return None
