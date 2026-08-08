from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import tempfile
from typing import Callable

from config import _db_path, _project_data_dir

_ALLOWED_GIT_PATH = "mimo-token-monitor/settings.db"
_COMMIT_MESSAGE = "chore(mimo-token-monitor): 同步悬浮窗设置"


class GitCommandError(RuntimeError):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = _sanitize_detail(detail)


def _sanitize_detail(detail: str) -> str:
    clean = re.sub(
        r"([A-Za-z][A-Za-z0-9+.-]*://)([^\s/@:]+):([^\s/@]+)@",
        r"\1***:***@",
        detail,
    )
    clean = re.sub(
        r"([A-Za-z][A-Za-z0-9+.-]*://)([^\s/@:]+)@",
        r"\1***@",
        clean,
    )
    sensitive_keys = "token|access_token|password|passwd|api_key|apikey|secret|credential|auth"
    clean = re.sub(
        r"(?i)(?<![\w])(remote\s+token)(\s+)[^\s,;&?#]+",
        r"\1\2***",
        clean,
    )
    clean = re.sub(
        rf"(?i)(?<![\w])({sensitive_keys})(\s*[=:]\s*)[^\s,;&?#]+",
        r"\1\2***",
        clean,
    )
    clean = re.sub(
        rf"(?i)([?#&](?:{sensitive_keys})=)[^&#\s]+",
        r"\1***",
        clean,
    )
    clean = re.sub(
        r"(Authorization:\s*Bearer\s+|(?<![\w])Bearer\s+)[^\s,;]+",
        r"\1***",
        clean,
        flags=re.I,
    )
    return clean[-2000:]


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
    def __init__(
        self,
        config: SyncConfig,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.config = config
        self._runner = runner

    def _git(self, *args: str, input_bytes: bytes | None = None, env: dict | None = None) -> bytes:
        command = ["git", "-C", str(self.config.repo_root.resolve()), *args]
        try:
            completed = self._runner(
                command,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitCommandError("Git 命令超时", str(exc)) from exc
        except OSError as exc:
            raise GitCommandError("无法启动 Git", str(exc)) from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace")
            raise GitCommandError("Git 命令执行失败", detail)
        return completed.stdout

    def validate_repository(self) -> SyncResult | None:
        invalid = self.validate_paths()
        if invalid is not None:
            return invalid
        try:
            actual = Path(
                self._git("rev-parse", "--show-toplevel").decode("utf-8").strip()
            ).resolve()
        except GitCommandError as exc:
            return SyncResult(SyncStatus.SKIPPED, "validate", str(exc), exc.detail)
        if actual != self.config.repo_root.resolve():
            return SyncResult(SyncStatus.SKIPPED, "validate", "Git 仓库根目录不匹配")
        return None

    def validate_paths(self) -> SyncResult | None:
        cfg = self.config
        if cfg.git_path != _ALLOWED_GIT_PATH or PurePosixPath(cfg.git_path).is_absolute():
            return SyncResult(SyncStatus.SKIPPED, "validate", "同步目标路径不受允许")

        repo_root = cfg.repo_root.resolve()
        data_dir = cfg.data_dir.resolve()
        db_path = cfg.db_path.resolve()
        if db_path.parent != data_dir:
            return SyncResult(SyncStatus.SKIPPED, "validate", "数据库不在指定数据目录")
        if db_path != data_dir / "settings.db":
            return SyncResult(SyncStatus.SKIPPED, "validate", "数据库路径不是允许的 settings.db")
        if data_dir.parent != repo_root:
            return SyncResult(SyncStatus.SKIPPED, "validate", "数据目录与仓库根目录不匹配")
        return None

    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/{self.config.remote}/{self.config.branch}"

    def _validate_sqlite(self, path: Path) -> bool:
        conn = None
        try:
            uri = f"{path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            row = conn.execute("PRAGMA quick_check").fetchone()
            return row == ("ok",)
        except sqlite3.Error:
            return False
        finally:
            if conn is not None:
                conn.close()


    @contextmanager
    def _temporary_index(self):
        fd, name = tempfile.mkstemp(prefix="mimo-git-index-")
        os.close(fd)
        os.unlink(name)
        try:
            yield Path(name)
        finally:
            Path(name).unlink(missing_ok=True)

    def _index_env(self, index_path: Path) -> dict:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index_path)
        return env

    def _build_commit(self, parent: str, index_path: Path) -> str | None:
        env = self._index_env(index_path)
        self._git("read-tree", parent, env=env)
        blob = self._git("hash-object", "-w", str(self.config.db_path)).decode().strip()
        self._git(
            "update-index", "--add", "--cacheinfo", "100644", blob,
            self.config.git_path, env=env,
        )
        tree = self._git("write-tree", env=env).decode().strip()
        parent_tree = self._git("rev-parse", f"{parent}^{{tree}}").decode().strip()
        if tree == parent_tree:
            return None
        return self._git(
            "commit-tree", tree, "-p", parent,
            input_bytes=(_COMMIT_MESSAGE + "\n").encode("utf-8"),
            env=env,
        ).decode().strip()

    def _is_non_fast_forward(self, exc: GitCommandError) -> bool:
        detail = exc.detail.lower()
        return "non-fast-forward" in detail or "fetch first" in detail

    def _before_push(self, attempt: int) -> None:
        pass

    def push_local_database(self) -> SyncResult:
        invalid = self.validate_repository()
        if invalid is not None:
            return invalid
        if not self.config.db_path.is_file() or not self._validate_sqlite(self.config.db_path):
            return SyncResult(SyncStatus.FAILED, "validate_db", "本地数据库不存在或校验失败")
        try:
            for attempt in range(1, self.config.push_retries + 1):
                self._git(
                    "fetch", self.config.remote,
                    f"{self.config.branch}:{self.remote_ref}",
                )
                parent = self._git("rev-parse", self.remote_ref).decode().strip()
                with self._temporary_index() as index_path:
                    commit = self._build_commit(parent, index_path)
                if commit is None:
                    return SyncResult(SyncStatus.NO_CHANGE, "push", "设置没有变化，无需推送")
                self._before_push(attempt)
                try:
                    self._git(
                        "push", self.config.remote,
                        f"{commit}:refs/heads/{self.config.branch}",
                    )
                except GitCommandError as exc:
                    if self._is_non_fast_forward(exc):
                        if attempt < self.config.push_retries:
                            continue
                        return SyncResult(
                            SyncStatus.FAILED,
                            "push",
                            "远端持续更新，已达到重试上限",
                            exc.detail,
                        )
                    raise
                self._git("update-ref", self.remote_ref, commit)
                return SyncResult(SyncStatus.SUCCESS, "push", "已同步本地悬浮窗设置")
        except (GitCommandError, OSError) as exc:
            detail = exc.detail if isinstance(exc, GitCommandError) else _sanitize_detail(str(exc))
            return SyncResult(SyncStatus.FAILED, "push", str(exc), detail)

    def pull_remote_database(self) -> SyncResult:
        invalid = self.validate_repository()
        if invalid is not None:
            return invalid
        temp_path: Path | None = None
        try:
            self._git("fetch", self.config.remote,
                      f"{self.config.branch}:{self.remote_ref}")
            blob = self._git("show", f"{self.remote_ref}:{self.config.git_path}")
            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(
                prefix=".mimo-settings-", suffix=".tmp", dir=self.config.data_dir
            )
            temp_path = Path(name)
            try:
                handle = os.fdopen(fd, "wb")
            except OSError:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            with handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            if not self._validate_sqlite(temp_path):
                return SyncResult(SyncStatus.FAILED, "validate_db", "远端数据库校验失败")
            os.replace(temp_path, self.config.db_path)
            temp_path = None
            return SyncResult(SyncStatus.SUCCESS, "pull", "已载入远端悬浮窗设置")
        except (GitCommandError, OSError) as exc:
            detail = exc.detail if isinstance(exc, GitCommandError) else _sanitize_detail(str(exc))
            return SyncResult(SyncStatus.FAILED, "pull", str(exc), detail)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
