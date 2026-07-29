# MiMo 设置数据库 Git 同步实施计划

> **状态：已完成。** 本文件保留为实施过程记录，其中分步代码片段不再作为现行接口或运维说明；当前行为以 `mimo-token-monitor/README.md`、`mimo-token-monitor/CLAUDE.md` 和同主题设计规格为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在进程首次显示悬浮窗前以远端 `settings.db` 覆盖本地有效副本，并在真正退出时只提交和推送本地 `mimo-token-monitor/settings.db`，不影响共享仓库中的其他程序数据。

**Architecture:** 新增标准库实现的 `data_sync.py`，以 Git plumbing 命令、临时 index、SQLite 校验和原子替换隔离同步逻辑；`sync_runtime.py` 用 `QThread` 包装阻塞同步并提供启动/退出编排。`main.py` 在 `load_config()` 前运行启动同步，`widget.py` 仅通过注入的退出回调发起同步，保持 UI 与 Git 业务分离。

**Tech Stack:** Python 3、PyQt6、SQLite（`sqlite3`）、Git CLI plumbing、`unittest`、标准库 `subprocess`/`tempfile`/`pathlib`。

## Global Constraints

- 平台为 Windows，默认数据文件是 `D:\python\data\mimo-token-monitor\settings.db`。
- 唯一允许同步的 Git 路径是 `mimo-token-monitor/settings.db`；不得修改、暂存、还原或删除 `financial-data-backup` 等兄弟目录。
- 启动同步远端优先；二进制 SQLite 不做合并；远端 DB 必须通过 `PRAGMA quick_check` 后才能原子覆盖本地文件。
- 真正退出时本机 DB 优先；每次推送都基于最新远端完整 tree 重建仅替换目标 blob 的提交，不使用无条件 force push。
- 启动失败继续使用本地 DB；退出失败保留本地 DB 并正常退出。
- Git 默认 remote 为 `origin`、branch 为 `main`；每次启动拉取或退出推送共享 `30` 秒总 operation deadline；push 最多尝试 `3` 次（首次尝试加最多 2 次竞争重试）。
- 不新增第三方依赖；沿用 `MIMO_TOKEN_MONITOR_DATA_DIR`，新增 `MIMO_TOKEN_MONITOR_GIT_REMOTE`、`MIMO_TOKEN_MONITOR_GIT_BRANCH`、`MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS`、`MIMO_TOKEN_MONITOR_GIT_PUSH_RETRIES`。
- 所有 Git 临时文件和临时 index 必须在 `finally` 清理；日志不得包含 Cookie、数据库内容、URL 用户名、密码或 token。
- Qt 主线程不得执行 Git 网络操作；重复启动只唤醒现有窗口；关闭窗口只隐藏到托盘。
- 所有 `QPainter` 坐标保持整数；本功能不修改绘制代码。

## 文件结构

- Create: `mimo-token-monitor/data_sync.py` — 同步配置、结构化结果、Git 命令封装、路径校验、SQLite 校验、启动拉取与退出提交/推送。
- Create: `mimo-token-monitor/sync_runtime.py` — `QThread` worker、启动同步等待、退出同步控制与通知。
- Create: `mimo-token-monitor/tests/test_data_sync.py` — 临时 bare remote 集成测试，验证路径隔离、远端覆盖、只提交目标文件及 push 竞争。
- Create: `mimo-token-monitor/tests/test_sync_runtime.py` — 使用 fake service 验证线程编排、结果回调和重复退出保护。
- Modify: `mimo-token-monitor/main.py` — 单实例通过后、`load_config()` 前运行启动同步，并把退出同步回调注入窗口。
- Modify: `mimo-token-monitor/widget.py` — 接收退出请求回调、展示启动同步失败通知、退出期间防重复触发。
- Modify: `mimo-token-monitor/README.md` — 记录同步策略、环境变量、Git/SSH 前置条件和失败降级。
- Modify: `mimo-token-monitor/AGENTS.md` — 更新模块清单、测试命令和同步架构约束。

---

### Task 1: 同步配置、结果类型与安全路径校验

**Files:**
- Create: `mimo-token-monitor/data_sync.py`
- Create: `mimo-token-monitor/tests/test_data_sync.py`

**Interfaces:**
- Consumes: `config._project_data_dir() -> str` 与 `config._db_path() -> str`。
- Produces: `SyncStatus(str, Enum)`、`SyncResult`、`SyncConfig.from_environment()`、`DataSyncService(config)`、`DataSyncService.validate_paths() -> SyncResult | None`。

- [ ] **Step 1: 写配置默认值、环境变量和越界路径的失败测试**

```python
# tests/test_data_sync.py
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_sync import DataSyncService, SyncConfig, SyncStatus


class TestSyncConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_sync_")
        self.repo = Path(self.tmp.name) / "data"
        self.data_dir = self.repo / "mimo-token-monitor"
        self.data_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_environment_defaults(self):
        with patch.dict(os.environ, {"MIMO_TOKEN_MONITOR_DATA_DIR": str(self.data_dir)}, clear=True):
            config = SyncConfig.from_environment()
        self.assertEqual(config.repo_root, self.repo.resolve())
        self.assertEqual(config.db_path, (self.data_dir / "settings.db").resolve())
        self.assertEqual(config.git_path, "mimo-token-monitor/settings.db")
        self.assertEqual(config.remote, "origin")
        self.assertEqual(config.branch, "main")
        self.assertEqual(config.timeout_seconds, 30)
        self.assertEqual(config.push_retries, 3)

    def test_invalid_numeric_environment_is_rejected(self):
        with patch.dict(os.environ, {
            "MIMO_TOKEN_MONITOR_DATA_DIR": str(self.data_dir),
            "MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS": "zero",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS"):
                SyncConfig.from_environment()

    def test_target_outside_data_directory_is_rejected(self):
        config = SyncConfig(
            repo_root=self.repo.resolve(),
            data_dir=self.data_dir.resolve(),
            db_path=(self.repo / "other" / "settings.db").resolve(),
            git_path="mimo-token-monitor/settings.db",
        )
        result = DataSyncService(config).validate_paths()
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        self.assertEqual(result.stage, "validate")

    def test_wrong_git_path_is_rejected(self):
        config = SyncConfig(
            repo_root=self.repo.resolve(),
            data_dir=self.data_dir.resolve(),
            db_path=(self.data_dir / "settings.db").resolve(),
            git_path="financial-data-backup/settings.db",
        )
        result = DataSyncService(config).validate_paths()
        self.assertEqual(result.status, SyncStatus.SKIPPED)
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestSyncConfig -v`

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'data_sync'`。

- [ ] **Step 3: 实现类型、环境配置与纯路径校验**

```python
# data_sync.py
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
        if cfg.db_path.parent != cfg.data_dir:
            return SyncResult(SyncStatus.SKIPPED, "validate", "数据库不在指定数据目录")
        if cfg.data_dir.parent != cfg.repo_root:
            return SyncResult(SyncStatus.SKIPPED, "validate", "数据目录与仓库根目录不匹配")
        return None
```

- [ ] **Step 4: 运行配置测试并确认通过**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestSyncConfig -v`

Expected: 4 tests PASS。

- [ ] **Step 5: 提交配置边界**

```bash
git add mimo-token-monitor/data_sync.py mimo-token-monitor/tests/test_data_sync.py
git commit -m "feat(data-sync): 添加同步配置与路径隔离"
```

### Task 2: Git 命令封装、仓库校验与安全诊断

**Files:**
- Modify: `mimo-token-monitor/data_sync.py`
- Modify: `mimo-token-monitor/tests/test_data_sync.py`

**Interfaces:**
- Consumes: Task 1 的 `SyncConfig`、`SyncResult`、`DataSyncService.validate_paths()`。
- Produces: `GitCommandError`、`DataSyncService.validate_repository()`、私有 `_git(*args, input_bytes=None, env=None) -> bytes`；后续拉取和推送共用。

- [ ] **Step 1: 写仓库根校验、超时及诊断过滤测试**

```python
# 追加到 tests/test_data_sync.py
import subprocess
from unittest.mock import Mock

from data_sync import GitCommandError, _sanitize_detail


class TestGitBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_git_")
        self.repo = Path(self.tmp.name) / "data"
        self.data_dir = self.repo / "mimo-token-monitor"
        self.data_dir.mkdir(parents=True)
        self.config = SyncConfig(
            repo_root=self.repo.resolve(),
            data_dir=self.data_dir.resolve(),
            db_path=(self.data_dir / "settings.db").resolve(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_repository_root_mismatch_is_skipped(self):
        runner = Mock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"C:/wrong/repo\n", stderr=b""
        ))
        result = DataSyncService(self.config, runner=runner).validate_repository()
        self.assertEqual(result.status, SyncStatus.SKIPPED)
        self.assertEqual(result.stage, "validate")

    def test_git_timeout_becomes_command_error(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired(["git"], 30))
        service = DataSyncService(self.config, runner=runner)
        with self.assertRaisesRegex(GitCommandError, "超时"):
            service._git("status")

    def test_sensitive_url_is_redacted_and_truncated(self):
        detail = "x" * 2100 + " https://alice:secret@example.test/path?token=abc"
        clean = _sanitize_detail(detail)
        self.assertLessEqual(len(clean), 2000)
        self.assertNotIn("alice", clean)
        self.assertNotIn("secret", clean)
        self.assertNotIn("abc", clean)
```

- [ ] **Step 2: 运行测试并确认签名和函数缺失**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestGitBoundary -v`

Expected: FAIL，包含 `ImportError` 或 `TypeError: DataSyncService.__init__() got an unexpected keyword argument 'runner'`。

- [ ] **Step 3: 实现受控 subprocess 和仓库根校验**

```python
# data_sync.py 新增 imports
import re
import subprocess
from typing import Callable


class GitCommandError(RuntimeError):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = _sanitize_detail(detail)


def _sanitize_detail(detail: str) -> str:
    clean = re.sub(r"https?://[^\s/@:]+:[^\s/@]+@", "https://***:***@", detail)
    clean = re.sub(r"([?&](?:token|access_token)=)[^&\s]+", r"\1***", clean, flags=re.I)
    return clean[-2000:]


class DataSyncService:
    def __init__(
        self,
        config: SyncConfig,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.config = config
        self._runner = runner

    # 保留 Task 1 的 validate_paths

    def _git(self, *args: str, input_bytes: bytes | None = None, env: dict | None = None) -> bytes:
        command = ["git", "-C", str(self.config.repo_root), *args]
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
        if actual != self.config.repo_root:
            return SyncResult(SyncStatus.SKIPPED, "validate", "Git 仓库根目录不匹配")
        return None
```

- [ ] **Step 4: 运行边界测试及现有配置测试**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync tests.test_config -v`

Expected: 所有测试 PASS。

- [ ] **Step 5: 提交 Git 执行边界**

```bash
git add mimo-token-monitor/data_sync.py mimo-token-monitor/tests/test_data_sync.py
git commit -m "feat(data-sync): 封装安全 Git 命令执行"
```

### Task 3: 启动时获取、校验并原子替换远端 SQLite

**Files:**
- Modify: `mimo-token-monitor/data_sync.py`
- Modify: `mimo-token-monitor/tests/test_data_sync.py`

**Interfaces:**
- Consumes: Task 2 的 `_git()` 和 `validate_repository()`。
- Produces: `DataSyncService.pull_remote_database() -> SyncResult`、私有 `_validate_sqlite(path: Path) -> bool`、`remote_ref` 属性。

- [ ] **Step 1: 建立真实临时 remote fixture 并写拉取测试**

```python
# tests/test_data_sync.py 新增辅助函数和测试类
import sqlite3


def run_git(cwd: Path, *args: str, input_bytes: bytes | None = None):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )


def write_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
        conn.execute("INSERT INTO settings VALUES ('cookie', ?)", (f'"{value}"',))


def read_cookie(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT value_json FROM settings WHERE key='cookie'").fetchone()[0]


class GitRepoFixture:
    def __init__(self, root: Path):
        self.remote = root / "remote.git"
        self.repo = root / "data"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run_git(self.repo, "config", "user.name", "MiMo Test")
        run_git(self.repo, "config", "user.email", "mimo@example.test")
        run_git(self.repo, "remote", "add", "origin", str(self.remote))
        write_db(self.repo / "mimo-token-monitor" / "settings.db", "remote-v1")
        other = self.repo / "financial-data-backup" / "keep.txt"
        other.parent.mkdir(parents=True)
        other.write_text("keep-v1", encoding="utf-8")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "seed")
        run_git(self.repo, "push", "-u", "origin", "main")

    def config(self) -> SyncConfig:
        data_dir = self.repo / "mimo-token-monitor"
        return SyncConfig(
            repo_root=self.repo.resolve(), data_dir=data_dir.resolve(),
            db_path=(data_dir / "settings.db").resolve(), timeout_seconds=10,
        )


class TestPullRemoteDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_pull_")
        self.fixture = GitRepoFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_remote_valid_database_overwrites_local_atomically(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        db.unlink()
        write_db(db, "local")
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.SUCCESS)
        self.assertEqual(read_cookie(db), '"remote-v1"')
        self.assertEqual(list(db.parent.glob(".mimo-settings-*.tmp")), [])

    def test_invalid_remote_database_preserves_local(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        write_db(db, "local")
        invalid = self.fixture.repo / "invalid.db"
        invalid.write_bytes(b"not sqlite")
        run_git(self.fixture.repo, "add", "invalid.db")
        # 用 update-index 把无效 blob 指向目标路径后提交并推送
        blob = run_git(self.fixture.repo, "hash-object", "-w", str(invalid)).stdout.strip().decode()
        run_git(self.fixture.repo, "update-index", "--cacheinfo", "100644", blob,
                "mimo-token-monitor/settings.db")
        run_git(self.fixture.repo, "commit", "-m", "invalid remote")
        run_git(self.fixture.repo, "push", "origin", "main")
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(read_cookie(db), '"local"')
```

- [ ] **Step 2: 运行拉取测试并确认方法不存在**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestPullRemoteDatabase -v`

Expected: FAIL，包含 `AttributeError: 'DataSyncService' object has no attribute 'pull_remote_database'`。

- [ ] **Step 3: 实现 fetch、blob 导出、SQLite 校验和原子替换**

```python
# data_sync.py 新增 imports
import sqlite3
import tempfile

# DataSyncService 内新增
    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/{self.config.remote}/{self.config.branch}"

    def _validate_sqlite(self, path: Path) -> bool:
        try:
            uri = f"{path.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                row = conn.execute("PRAGMA quick_check").fetchone()
            return row == ("ok",)
        except sqlite3.Error:
            return False

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
            with os.fdopen(fd, "wb") as handle:
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
                temp_path.unlink(missing_ok=True)
```

- [ ] **Step 4: 运行拉取及全部数据同步测试**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync -v`

Expected: 所有测试 PASS；测试日志中的 Git 命令均只作用于临时目录。

- [ ] **Step 5: 提交启动拉取实现**

```bash
git add mimo-token-monitor/data_sync.py mimo-token-monitor/tests/test_data_sync.py
git commit -m "feat(data-sync): 启动时原子载入远端数据库"
```

### Task 4: 用临时 index 创建仅替换目标 DB 的提交

**Files:**
- Modify: `mimo-token-monitor/data_sync.py`
- Modify: `mimo-token-monitor/tests/test_data_sync.py`

**Interfaces:**
- Consumes: Task 3 的 `remote_ref`、`_git()` 与路径校验。
- Produces: `DataSyncService.push_local_database() -> SyncResult`、私有 `_build_commit(parent: str, index_path: Path) -> str | None`。

- [ ] **Step 1: 写只改变 DB、保留其他目录与不污染共享 index 的测试**

```python
# tests/test_data_sync.py 追加
class TestPushLocalDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mimo_push_")
        self.fixture = GitRepoFixture(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_push_changes_only_target_and_preserves_other_worktree_changes(self):
        repo = self.fixture.repo
        db = repo / "mimo-token-monitor" / "settings.db"
        write_db(db, "local-exit")
        other = repo / "financial-data-backup" / "keep.txt"
        other.write_text("local-uncommitted", encoding="utf-8")
        untracked = repo / "financial-data-backup" / "running.tmp"
        untracked.write_text("busy", encoding="utf-8")
        status_before = run_git(repo, "status", "--porcelain=v1").stdout

        result = DataSyncService(self.fixture.config()).push_local_database()

        self.assertEqual(result.status, SyncStatus.SUCCESS)
        self.assertEqual(run_git(repo, "status", "--porcelain=v1").stdout, status_before)
        changed = run_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r",
                          "refs/remotes/origin/main").stdout.decode().splitlines()
        self.assertEqual(changed, ["mimo-token-monitor/settings.db"])
        self.assertEqual(other.read_text(encoding="utf-8"), "local-uncommitted")
        self.assertEqual(untracked.read_text(encoding="utf-8"), "busy")

    def test_unchanged_database_creates_no_commit(self):
        service = DataSyncService(self.fixture.config())
        before = run_git(self.fixture.repo, "rev-parse", "refs/remotes/origin/main").stdout
        result = service.push_local_database()
        after = run_git(self.fixture.repo, "rev-parse", "refs/remotes/origin/main").stdout
        self.assertEqual(result.status, SyncStatus.NO_CHANGE)
        self.assertEqual(after, before)
```

- [ ] **Step 2: 运行推送测试并确认方法不存在**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestPushLocalDatabase -v`

Expected: FAIL，包含 `AttributeError: 'DataSyncService' object has no attribute 'push_local_database'`。

- [ ] **Step 3: 实现临时 index、blob/tree/commit 构造和普通 fast-forward push**

```python
# data_sync.py 新增
from contextlib import contextmanager

_COMMIT_MESSAGE = "chore(mimo-token-monitor): 同步悬浮窗设置"

# DataSyncService 内新增
    @contextmanager
    def _temporary_index(self):
        fd, name = tempfile.mkstemp(prefix="mimo-git-index-")
        os.close(fd)
        os.unlink(name)  # read-tree 要求 index 不存在或有效
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
        self._git("update-index", "--add", "--cacheinfo", "100644", blob,
                  self.config.git_path, env=env)
        tree = self._git("write-tree", env=env).decode().strip()
        parent_tree = self._git("rev-parse", f"{parent}^{{tree}}").decode().strip()
        if tree == parent_tree:
            return None
        return self._git(
            "commit-tree", tree, "-p", parent,
            input_bytes=(_COMMIT_MESSAGE + "\n").encode("utf-8"),
            env=env,
        ).decode().strip()

    def push_local_database(self) -> SyncResult:
        invalid = self.validate_repository()
        if invalid is not None:
            return invalid
        if not self.config.db_path.is_file() or not self._validate_sqlite(self.config.db_path):
            return SyncResult(SyncStatus.FAILED, "validate_db", "本地数据库不存在或校验失败")
        try:
            self._git("fetch", self.config.remote,
                      f"{self.config.branch}:{self.remote_ref}")
            parent = self._git("rev-parse", self.remote_ref).decode().strip()
            with self._temporary_index() as index_path:
                commit = self._build_commit(parent, index_path)
            if commit is None:
                return SyncResult(SyncStatus.NO_CHANGE, "push", "设置没有变化，无需推送")
            self._git("push", self.config.remote,
                      f"{commit}:refs/heads/{self.config.branch}")
            self._git("update-ref", self.remote_ref, commit)
            return SyncResult(SyncStatus.SUCCESS, "push", "已同步本地悬浮窗设置")
        except (GitCommandError, OSError) as exc:
            detail = exc.detail if isinstance(exc, GitCommandError) else _sanitize_detail(str(exc))
            return SyncResult(SyncStatus.FAILED, "push", str(exc), detail)
```

- [ ] **Step 4: 运行推送测试并检查共享工作树状态保持一致**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestPushLocalDatabase -v`

Expected: 2 tests PASS；`status_before == status_after` 断言通过。

- [ ] **Step 5: 提交精确推送实现**

```bash
git add mimo-token-monitor/data_sync.py mimo-token-monitor/tests/test_data_sync.py
git commit -m "feat(data-sync): 仅提交并推送悬浮窗数据库"
```

### Task 5: 处理远端并发更新并保留其他路径最新内容

**Files:**
- Modify: `mimo-token-monitor/data_sync.py`
- Modify: `mimo-token-monitor/tests/test_data_sync.py`

**Interfaces:**
- Consumes: Task 4 的 `_build_commit()`。
- Produces: `DataSyncService.push_local_database()` 的最多 `push_retries` 次 fetch/rebuild/push 行为；仅 non-fast-forward 类失败重试，其他失败立即返回。

- [ ] **Step 1: 写并发远端提交下的重建测试**

```python
# tests/test_data_sync.py 追加辅助方法与测试
class RacingSyncService(DataSyncService):
    def __init__(self, config: SyncConfig, competing_clone: Path):
        super().__init__(config)
        self.competing_clone = competing_clone
        self.injected = False

    def _before_push(self, attempt: int) -> None:
        if self.injected:
            return
        self.injected = True
        other = self.competing_clone / "financial-data-backup" / "remote.txt"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("remote-latest", encoding="utf-8")
        run_git(self.competing_clone, "add", ".")
        run_git(self.competing_clone, "commit", "-m", "concurrent other data")
        run_git(self.competing_clone, "push", "origin", "main")


# 追加到 TestPushLocalDatabase
    def test_push_rebuilds_on_remote_race_and_preserves_remote_other_path(self):
        clone = Path(self.tmp.name) / "competing"
        subprocess.run(["git", "clone", "-b", "main", str(self.fixture.remote), str(clone)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run_git(clone, "config", "user.name", "Competitor")
        run_git(clone, "config", "user.email", "competitor@example.test")
        write_db(self.fixture.repo / "mimo-token-monitor" / "settings.db", "local-exit")

        result = RacingSyncService(self.fixture.config(), clone).push_local_database()

        self.assertEqual(result.status, SyncStatus.SUCCESS)
        verify = Path(self.tmp.name) / "verify"
        subprocess.run(["git", "clone", "-b", "main", str(self.fixture.remote), str(verify)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(read_cookie(verify / "mimo-token-monitor" / "settings.db"), '"local-exit"')
        self.assertEqual((verify / "financial-data-backup" / "remote.txt").read_text(encoding="utf-8"),
                         "remote-latest")
```

- [ ] **Step 2: 运行竞争测试并确认首次 push 失败**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestPushLocalDatabase.test_push_rebuilds_on_remote_race_and_preserves_remote_other_path -v`

Expected: FAIL，结果为 `SyncStatus.FAILED`，远端 DB 未更新为 `local-exit`。

- [ ] **Step 3: 抽出重试循环，仅对 non-fast-forward 重建提交**

```python
# DataSyncService 内新增 hook，生产环境为空操作
    def _before_push(self, attempt: int) -> None:
        pass

    def _is_non_fast_forward(self, exc: GitCommandError) -> bool:
        detail = exc.detail.lower()
        return "non-fast-forward" in detail or "fetch first" in detail or "rejected" in detail

# 用以下核心循环替换 Task 4 push_local_database 中 fetch 到 push 的逻辑
        try:
            for attempt in range(1, self.config.push_retries + 1):
                self._git("fetch", self.config.remote,
                          f"{self.config.branch}:{self.remote_ref}")
                parent = self._git("rev-parse", self.remote_ref).decode().strip()
                with self._temporary_index() as index_path:
                    commit = self._build_commit(parent, index_path)
                if commit is None:
                    return SyncResult(SyncStatus.NO_CHANGE, "push", "设置没有变化，无需推送")
                self._before_push(attempt)
                try:
                    self._git("push", self.config.remote,
                              f"{commit}:refs/heads/{self.config.branch}")
                except GitCommandError as exc:
                    if attempt < self.config.push_retries and self._is_non_fast_forward(exc):
                        continue
                    raise
                self._git("update-ref", self.remote_ref, commit)
                return SyncResult(SyncStatus.SUCCESS, "push", "已同步本地悬浮窗设置")
            return SyncResult(SyncStatus.FAILED, "push", "远端持续更新，已停止重试")
        except (GitCommandError, OSError) as exc:
            detail = exc.detail if isinstance(exc, GitCommandError) else _sanitize_detail(str(exc))
            return SyncResult(SyncStatus.FAILED, "push", str(exc), detail)
```

- [ ] **Step 4: 运行完整同步测试套件**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync -v`

Expected: 所有测试 PASS，包括远端 `remote.txt` 和本机退出 DB 同时保留。

- [ ] **Step 5: 提交并发推送处理**

```bash
git add mimo-token-monitor/data_sync.py mimo-token-monitor/tests/test_data_sync.py
git commit -m "fix(data-sync): 并发更新时重建目标数据库提交"
```

### Task 6: Qt 工作线程与启动同步编排

**Files:**
- Create: `mimo-token-monitor/sync_runtime.py`
- Create: `mimo-token-monitor/tests/test_sync_runtime.py`
- Modify: `mimo-token-monitor/main.py:4-9,72-109`

**Interfaces:**
- Consumes: `DataSyncService.pull_remote_database()`、`SyncResult`、`SyncStatus`。
- Produces: `SyncWorker(QThread)`、`run_startup_sync(service, app) -> SyncResult`；`main()` 保证它发生在 `load_config()` 之前。

- [ ] **Step 1: 写 worker 在线程执行及 main 调用顺序测试**

```python
# tests/test_sync_runtime.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication

from data_sync import SyncResult, SyncStatus
from sync_runtime import run_startup_sync


class FakeService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def pull_remote_database(self):
        self.calls.append("pull")
        return self.result


class TestStartupRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_startup_sync_returns_worker_result(self):
        expected = SyncResult(SyncStatus.SUCCESS, "pull", "ok")
        service = FakeService(expected)
        self.assertEqual(run_startup_sync(service, self.app), expected)
        self.assertEqual(service.calls, ["pull"])

    @patch("main.load_config", return_value={"cookie": "x"})
    @patch("main.run_startup_sync")
    def test_initialize_window_syncs_before_loading_config(self, run_sync, load_config):
        calls = []
        run_sync.side_effect = lambda *args: calls.append("sync") or SyncResult(
            SyncStatus.SUCCESS, "pull", "ok")
        load_config.side_effect = lambda: calls.append("load") or {"cookie": "x"}
        from main import initialize_window
        with patch("main.TokenWidget") as widget_type:
            widget_type.return_value.show = Mock(side_effect=lambda: calls.append("show"))
            widget, result = initialize_window(self.app, Mock())
        self.assertIs(widget, widget_type.return_value)
        self.assertEqual(result.status, SyncStatus.SUCCESS)
        self.assertEqual(calls, ["sync", "load", "show"])
```

- [ ] **Step 2: 运行 runtime 测试并确认模块或函数不存在**

Run: `cd mimo-token-monitor && python -m unittest tests.test_sync_runtime -v`

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'sync_runtime'`。

- [ ] **Step 3: 实现一次性工作线程及同步等待函数**

```python
# sync_runtime.py
from __future__ import annotations

from collections.abc import Callable
from PyQt6.QtCore import QEventLoop, QThread, pyqtSignal

from data_sync import DataSyncService, SyncResult, SyncStatus


class SyncWorker(QThread):
    completed = pyqtSignal(object)

    def __init__(self, operation: Callable[[], SyncResult], parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
        except Exception as exc:
            result = SyncResult(SyncStatus.FAILED, "runtime", "同步线程异常", str(exc)[-2000:])
        self.completed.emit(result)


def run_startup_sync(service: DataSyncService, app) -> SyncResult:
    loop = QEventLoop()
    holder = {}
    worker = SyncWorker(service.pull_remote_database, app)

    def complete(result: SyncResult):
        holder["result"] = result
        loop.quit()

    worker.completed.connect(complete)
    worker.start()
    loop.exec()
    worker.wait()
    return holder["result"]
```

```python
# main.py 新增 imports
from data_sync import DataSyncService, SyncConfig, SyncResult, SyncStatus
from sync_runtime import run_startup_sync


def build_sync_service() -> tuple[DataSyncService | None, SyncResult | None]:
    try:
        return DataSyncService(SyncConfig.from_environment()), None
    except ValueError as exc:
        return None, SyncResult(SyncStatus.SKIPPED, "config", str(exc))


def initialize_window(app, service):
    startup_result = (
        run_startup_sync(service, app)
        if service is not None
        else SyncResult(SyncStatus.SKIPPED, "config", "同步配置无效")
    )
    cfg = load_config()
    # 保留现有首次配置流程；取消时返回 (None, startup_result)
    if not cfg.get("cookie"):
        dlg = SettingsDialog(cfg)
        dlg.setWindowTitle("MiMo Token - 首次配置")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = dlg.get_config()
            save_config(cfg)
        else:
            return None, startup_result
    widget = TokenWidget(cfg)
    widget.show()
    return widget, startup_result
```

将 `main()` 中现有 `load_config()` 到 `widget.show()` 替换为：

```python
        service, config_result = build_sync_service()
        widget, startup_result = initialize_window(app, service)
        if config_result is not None:
            startup_result = config_result
        if widget is None:
            return 0
```

- [ ] **Step 4: 运行 runtime、同步和配置测试**

Run: `cd mimo-token-monitor && python -m unittest tests.test_sync_runtime tests.test_data_sync tests.test_config -v`

Expected: 所有测试 PASS；调用顺序为 `sync → load → show`。

- [ ] **Step 5: 提交启动生命周期集成**

```bash
git add mimo-token-monitor/sync_runtime.py mimo-token-monitor/main.py mimo-token-monitor/tests/test_sync_runtime.py
git commit -m "feat(data-sync): 显示窗口前后台拉取设置"
```

### Task 7: 真正退出时异步推送并防止重复退出

**Files:**
- Modify: `mimo-token-monitor/sync_runtime.py`
- Modify: `mimo-token-monitor/main.py:97-109`
- Modify: `mimo-token-monitor/widget.py:247-302,891-916`
- Modify: `mimo-token-monitor/tests/test_sync_runtime.py`

**Interfaces:**
- Consumes: `DataSyncService.push_local_database()` 与 Task 6 的 `SyncWorker`。
- Produces: `ExitSyncController(QObject).request_exit()`；`TokenWidget(cfg, exit_callback=None, startup_sync_result=None)`；真正退出路径调用 callback，`closeEvent` 保持只隐藏。

- [ ] **Step 1: 写退出回调、防重复触发和失败后仍退出测试**

```python
# tests/test_sync_runtime.py 追加 imports/tests
from PyQt6.QtCore import QEventLoop, QTimer
from sync_runtime import ExitSyncController


class FakePushService:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def push_local_database(self):
        self.calls += 1
        return self.result


class TestExitRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_duplicate_exit_request_starts_one_push_and_quits_after_failure(self):
        service = FakePushService(SyncResult(SyncStatus.FAILED, "push", "offline"))
        quit_callback = Mock()
        notify_callback = Mock()
        controller = ExitSyncController(service, quit_callback, notify_callback)
        loop = QEventLoop()
        controller.finished.connect(lambda _result: loop.quit())

        controller.request_exit()
        controller.request_exit()
        QTimer.singleShot(5000, loop.quit)
        loop.exec()

        self.assertEqual(service.calls, 1)
        notify_callback.assert_called_once()
        quit_callback.assert_called_once()

    def test_no_service_quits_immediately(self):
        quit_callback = Mock()
        controller = ExitSyncController(None, quit_callback, Mock())
        controller.request_exit()
        quit_callback.assert_called_once()
```

- [ ] **Step 2: 运行退出测试并确认 controller 不存在**

Run: `cd mimo-token-monitor && python -m unittest tests.test_sync_runtime.TestExitRuntime -v`

Expected: FAIL，包含 `ImportError: cannot import name 'ExitSyncController'`。

- [ ] **Step 3: 实现退出控制器并保持 worker 引用**

```python
# sync_runtime.py 新增 imports
from PyQt6.QtCore import QObject


class ExitSyncController(QObject):
    finished = pyqtSignal(object)

    def __init__(self, service, quit_callback, notify_callback, parent=None):
        super().__init__(parent)
        self.service = service
        self.quit_callback = quit_callback
        self.notify_callback = notify_callback
        self._worker = None
        self._exiting = False

    def request_exit(self):
        if self._exiting:
            return
        self._exiting = True
        if self.service is None:
            self.quit_callback()
            return
        self._worker = SyncWorker(self.service.push_local_database, self)
        self._worker.completed.connect(self._complete)
        self._worker.start()

    def _complete(self, result: SyncResult):
        if result.status in {SyncStatus.FAILED, SyncStatus.SKIPPED}:
            self.notify_callback(result)
        self.finished.emit(result)
        self.quit_callback()
```

- [ ] **Step 4: 将退出回调和启动失败通知接入窗口**

```python
# widget.py 修改构造签名和字段
    def __init__(self, cfg: dict, exit_callback=None, startup_sync_result=None):
        super().__init__()
        self.cfg = cfg
        self._exit_callback = exit_callback
        self._exit_requested = False
        # 保留现有初始化
        self._setup_tray()
        if startup_sync_result is not None and not startup_sync_result.ok:
            QTimer.singleShot(0, lambda: self._show_sync_result(startup_sync_result))

    def _show_sync_result(self, result):
        self._tray_icon.showMessage(
            "MiMo 设置同步",
            result.message,
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def _quit_app(self):
        """真正退出应用程序；同步期间忽略重复请求。"""
        if self._exit_requested:
            return
        self._exit_requested = True
        if self._exit_callback is not None:
            self._exit_callback()
        else:
            self._tray_icon.hide()
            QApplication.quit()

    def finish_quit(self):
        self._tray_icon.hide()
        QApplication.quit()
```

```python
# main.py 在 widget 建立后创建控制器并注入
from sync_runtime import ExitSyncController, run_startup_sync

# initialize_window 改为接收 service，并在构造前定义 holder
    widget = TokenWidget(cfg, startup_sync_result=startup_result)
    controller = ExitSyncController(
        service,
        widget.finish_quit,
        widget._show_sync_result,
        parent=widget,
    )
    widget._exit_callback = controller.request_exit
    widget._exit_sync_controller = controller
    widget.show()
```

保留 `closeEvent()` 的 `event.ignore(); self.hide()`，不调用退出回调。

- [ ] **Step 5: 运行 runtime 测试并手动核对退出路径**

Run: `cd mimo-token-monitor && python -m unittest tests.test_sync_runtime -v`

Expected: 所有测试 PASS；失败结果也会调用通知一次并最终调用 quit 一次。

Run: `cd mimo-token-monitor && python -m unittest discover -s tests -v`

Expected: 全部测试 PASS。

- [ ] **Step 6: 提交退出生命周期集成**

```bash
git add mimo-token-monitor/sync_runtime.py mimo-token-monitor/main.py mimo-token-monitor/widget.py mimo-token-monitor/tests/test_sync_runtime.py
git commit -m "feat(data-sync): 真正退出时后台推送设置"
```

### Task 8: 完善生命周期回归测试与同步失败降级

**Files:**
- Modify: `mimo-token-monitor/tests/test_sync_runtime.py`
- Modify: `mimo-token-monitor/tests/test_data_sync.py`
- Modify: `mimo-token-monitor/main.py`
- Modify: `mimo-token-monitor/widget.py`

**Interfaces:**
- Consumes: Tasks 1-7 的公开接口。
- Produces: 对重复实例、启动失败继续加载、关闭到托盘不推送、真正退出才推送的自动回归保护。

- [ ] **Step 1: 写启动失败仍加载和关闭事件不推送的测试**

```python
# tests/test_sync_runtime.py 追加
from PyQt6.QtGui import QCloseEvent
from widget import TokenWidget


class TestLifecycleDegradation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @patch("main.load_config", return_value={"cookie": "configured"})
    @patch("main.run_startup_sync", return_value=SyncResult(
        SyncStatus.FAILED, "pull", "网络不可用"
    ))
    def test_failed_startup_sync_still_creates_widget(self, _sync, _load):
        from main import initialize_window
        with patch("main.TokenWidget") as widget_type:
            widget, result = initialize_window(self.app, Mock())
        self.assertIs(widget, widget_type.return_value)
        self.assertEqual(result.status, SyncStatus.FAILED)

    def test_close_event_hides_without_requesting_exit(self):
        callback = Mock()
        cfg = {"cookie": "x", "position": [100, 100], "refresh_interval": 300,
               "opacity": 0.85, "always_on_top": True}
        with patch.object(TokenWidget, "_setup_tray"), patch("widget.save_config"):
            widget = TokenWidget(cfg, exit_callback=callback)
            widget._tray_icon = Mock()
            event = QCloseEvent()
            widget.closeEvent(event)
        self.assertFalse(event.isAccepted())
        callback.assert_not_called()

    def test_quit_action_requests_push_once(self):
        callback = Mock()
        cfg = {"cookie": "x", "position": [100, 100], "refresh_interval": 300,
               "opacity": 0.85, "always_on_top": True}
        with patch.object(TokenWidget, "_setup_tray"), patch("widget.save_config"):
            widget = TokenWidget(cfg, exit_callback=callback)
            widget._quit_app()
            widget._quit_app()
        callback.assert_called_once()
```

- [ ] **Step 2: 写 fetch/远端目标缺失和推送失败保留 DB 测试**

```python
# tests/test_data_sync.py 追加到对应测试类
    def test_missing_remote_target_preserves_local(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        write_db(db, "local-safe")
        run_git(self.fixture.repo, "rm", "mimo-token-monitor/settings.db")
        run_git(self.fixture.repo, "commit", "-m", "remove target")
        run_git(self.fixture.repo, "push", "origin", "main")
        result = DataSyncService(self.fixture.config()).pull_remote_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(read_cookie(db), '"local-safe"')

    def test_push_failure_preserves_local_database(self):
        db = self.fixture.repo / "mimo-token-monitor" / "settings.db"
        write_db(db, "local-safe")
        run_git(self.fixture.repo, "remote", "set-url", "origin",
                str(Path(self.tmp.name) / "missing.git"))
        result = DataSyncService(self.fixture.config()).push_local_database()
        self.assertEqual(result.status, SyncStatus.FAILED)
        self.assertEqual(read_cookie(db), '"local-safe"')
```

- [ ] **Step 3: 按确定接口补全生命周期实现并运行新增测试**

确保 `initialize_window()` 使用以下完整实现：

```python
def initialize_window(app, service):
    startup_result = (
        run_startup_sync(service, app)
        if service is not None
        else SyncResult(SyncStatus.SKIPPED, "config", "同步配置无效")
    )
    cfg = load_config()
    if not cfg.get("cookie"):
        dlg = SettingsDialog(cfg)
        dlg.setWindowTitle("MiMo Token - 首次配置")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None, startup_result
        cfg = dlg.get_config()
        save_config(cfg)
    widget = TokenWidget(cfg, startup_sync_result=startup_result)
    widget.show()
    return widget, startup_result
```

Run: `cd mimo-token-monitor && python -m unittest tests.test_sync_runtime.TestLifecycleDegradation tests.test_data_sync -v`

Expected: 所有测试 PASS；同步失败时仍按 `sync → load → show` 返回可用窗口。

- [ ] **Step 4: 验证重复启动在同步构造前返回**

在 `tests/test_sync_runtime.py` 追加：

```python
    @patch("main.run_startup_sync")
    @patch("main.build_sync_service")
    @patch("main.activate_existing_instance")
    @patch("main.check_single_instance", return_value=None)
    def test_duplicate_instance_returns_before_sync(
        self, _check, activate, build_service, run_sync
    ):
        from main import main
        self.assertEqual(main(), 0)
        activate.assert_called_once()
        build_service.assert_not_called()
        run_sync.assert_not_called()
```

确保 `main()` 保持以下顺序：

```python
def main() -> int:
    mutex = check_single_instance()
    if mutex is None:
        activate_existing_instance()
        return 0
    activation_event = create_activation_event()
    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        service, config_result = build_sync_service()
        widget, startup_result = initialize_window(app, service)
        if widget is None:
            return 0
        controller = ExitSyncController(
            service,
            widget.finish_quit,
            widget._show_sync_result,
            parent=widget,
        )
        widget._exit_callback = controller.request_exit
        widget._exit_sync_controller = controller
        # 接着创建现有 activation_timer，并返回 app.exec()
```

Run: `cd mimo-token-monitor && python -m unittest tests.test_sync_runtime.TestLifecycleDegradation.test_duplicate_instance_returns_before_sync -v`

Expected: PASS；`activate_existing_instance()` 调用一次，两个同步 mock 均未调用。

- [ ] **Step 5: 运行全部自动化测试**

Run: `cd mimo-token-monitor && python -m unittest discover -s tests -v`

Expected: 全部测试 PASS，无真实远端访问。

- [ ] **Step 6: 提交生命周期回归覆盖**

```bash
git add mimo-token-monitor/main.py mimo-token-monitor/widget.py mimo-token-monitor/tests/test_sync_runtime.py mimo-token-monitor/tests/test_data_sync.py
git commit -m "test(data-sync): 覆盖同步失败与窗口生命周期"
```

### Task 9: 更新文档与项目规则

**Files:**
- Modify: `mimo-token-monitor/README.md:22-43,108-160`
- Modify: `mimo-token-monitor/AGENTS.md:8-36,51-53`

**Interfaces:**
- Consumes: 已实现的环境变量、状态策略、命令和模块名。
- Produces: 用户操作与维护文档；不产生代码接口。

- [ ] **Step 1: 更新 README 的功能、同步行为和前置条件**

在功能列表加入：

```markdown
- **设置数据库 Git 同步**：进程首次启动前拉取远端 `mimo-token-monitor/settings.db`，真正退出时仅提交并推送该文件；关闭到托盘不推送
```

将“数据存储与跨设备同步”扩展为：

```markdown
### Git 同步策略

- `D:\python\data` 必须是 Git 仓库，并配置可访问的 `origin/main`；认证沿用本机 Git/SSH 配置。
- 启动时远端 `settings.db` 优先。远端文件通过 SQLite `PRAGMA quick_check` 后才会原子覆盖本地文件；同步失败时继续使用本地数据库。
- 只有托盘或悬浮窗菜单中的“退出”会推送；最小化到托盘不会推送。
- 退出时本机 `settings.db` 优先。远端并发更新时，程序基于最新远端 tree 重建提交，只替换 `mimo-token-monitor/settings.db`，保留其他目录的最新内容。
- 推送失败时本地数据库保持不变，程序仍正常退出。
- 程序不会对共享仓库执行 `git pull`、`checkout`、`reset`、`clean` 或普通工作树提交，不会暂存或还原 `financial-data-backup`。

可用环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MIMO_TOKEN_MONITOR_DATA_DIR` | `D:\python\data\mimo-token-monitor` | 数据目录；其父目录被视为仓库根目录 |
| `MIMO_TOKEN_MONITOR_GIT_REMOTE` | `origin` | Git 远端名 |
| `MIMO_TOKEN_MONITOR_GIT_BRANCH` | `main` | Git 分支名 |
| `MIMO_TOKEN_MONITOR_GIT_TIMEOUT_SECONDS` | `30` | 每次启动/退出同步的总 Git operation 预算秒数 |
| `MIMO_TOKEN_MONITOR_GIT_PUSH_RETRIES` | `3` | 最多 push 尝试次数；默认 3 表示首次尝试加最多 2 次竞争重试 |
```

- [ ] **Step 2: 更新 AGENTS.md 中过时测试说明和模块结构**

将当时过期的测试说明替换为：

```markdown
# 运行全部 unittest
python -m unittest discover -s tests -v
```

在架构清单加入：

```markdown
- **data_sync.py** — 以 Git plumbing、临时 index 和 SQLite 校验同步唯一目标 `mimo-token-monitor/settings.db`，不得操作共享仓库其他路径。
- **sync_runtime.py** — 用 QThread 编排启动拉取与真正退出推送，保证 Git 网络操作不阻塞 Qt 主线程。
```

在关键约束加入：

```markdown
- 设置 Git 同步只允许操作 `mimo-token-monitor/settings.db`；禁止用会修改共享工作树的 `git pull`、`checkout`、`reset`、`clean` 或普通 `git commit` 替代 plumbing 实现。
```

- [ ] **Step 3: 检查文档中的旧同步说法与实现一致**

Run: `git diff --check`

Expected: 无输出。

Run: `git diff -- mimo-token-monitor/README.md mimo-token-monitor/AGENTS.md`

Expected: 文档明确区分“关闭到托盘”和“真正退出”，且不再只建议使用同步盘。

- [ ] **Step 4: 提交文档**

```bash
git add mimo-token-monitor/README.md mimo-token-monitor/AGENTS.md
git commit -m "docs(data-sync): 记录数据库 Git 同步策略"
```

### Task 10: 全量验证与真实应用冒烟测试

**Files:**
- Verify: `mimo-token-monitor/data_sync.py`
- Verify: `mimo-token-monitor/sync_runtime.py`
- Verify: `mimo-token-monitor/main.py`
- Verify: `mimo-token-monitor/widget.py`
- Verify: `mimo-token-monitor/tests/test_data_sync.py`
- Verify: `mimo-token-monitor/tests/test_sync_runtime.py`
- Verify: `mimo-token-monitor/tests/test_config.py`

**Interfaces:**
- Consumes: 全部实施结果。
- Produces: 经自动化与真实应用验证的功能；不新增接口。

- [ ] **Step 1: 运行完整 unittest**

Run: `cd mimo-token-monitor && python -m unittest discover -s tests -v`

Expected: 全部测试 PASS；无测试访问 `D:\python\data` 或 GitHub。

- [ ] **Step 2: 编译检查所有 Python 文件**

Run: `cd mimo-token-monitor && python -m compileall -q .`

Expected: 退出码 0，无输出。

- [ ] **Step 3: 检查工作区和补丁格式**

Run: `git status --short && git diff --check`

Expected: 只存在当前任务预期文件改动；`git diff --check` 无输出。

- [ ] **Step 4: 用隔离临时仓库运行端到端同步脚本**

Run: `cd mimo-token-monitor && python -m unittest tests.test_data_sync.TestPullRemoteDatabase tests.test_data_sync.TestPushLocalDatabase -v`

Expected: 全部 PASS；验证远端覆盖、目标文件推送、竞争重建与兄弟目录隔离。

- [ ] **Step 5: 使用 run 技能启动真实应用做 UI 冒烟测试**

在执行阶段调用 `run` 技能，以测试环境变量把 `MIMO_TOKEN_MONITOR_DATA_DIR` 指向临时 fixture 数据目录后启动 `python main.py`，验证：

1. 窗口成功显示且可从托盘恢复。
2. 点击最小化后进程仍运行且未触发 push。
3. 点击“退出”后只产生目标 DB 同步提交。
4. 启动同步失败时显示警告但窗口仍可用。

Expected: 四项均通过，不操作真实 `D:\python\data`。

- [ ] **Step 6: 请求代码审查并修复确认的问题**

调用 `superpowers:requesting-code-review`，审查重点为：

- Git 命令是否可能修改共享工作树或 index。
- 临时 index 是否在所有异常路径清理。
- push 重试是否保留最新远端非目标路径。
- SQLite 校验与原子替换是否可能破坏本地 DB。
- Qt worker 生命周期是否存在提前销毁、重复退出或主线程阻塞。

Expected: 所有确认的问题修复后重新运行 Steps 1-5。

- [ ] **Step 7: 提交验证阶段修正**

若审查产生修正：

```bash
git add mimo-token-monitor
git commit -m "fix(data-sync): 修正同步审查问题"
```

若无修正则不创建空提交。
