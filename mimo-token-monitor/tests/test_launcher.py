from pathlib import Path
from unittest.mock import patch
import unittest

from code_sync import CODE_SYNC_RESULT_ENV
from data_sync import SyncResult, SyncStatus
from launcher import main, start_monitor


class TestLauncher(unittest.TestCase):
    def test_start_monitor_passes_sync_result_to_child_environment(self):
        project_root = Path("C:/workspace/mimo-token-monitor")
        result = SyncResult(SyncStatus.SUCCESS, "code_pull", "代码已更新")
        with patch("launcher.subprocess.Popen") as popen:
            start_monitor(["python.exe"], project_root, result)

        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["cwd"], project_root)
        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertIn(CODE_SYNC_RESULT_ENV, kwargs["env"])
        self.assertIn("代码已更新", kwargs["env"][CODE_SYNC_RESULT_ENV])

    @patch("launcher.start_monitor")
    @patch("launcher.local_python_candidates", return_value=[["python.exe"]])
    @patch("launcher.run_startup_code_sync")
    @patch("launcher.find_project_root")
    def test_main_syncs_code_before_spawning_monitor(
        self, find_project_root, run_startup, _candidates, start_monitor
    ):
        project_root = Path("C:/workspace/mimo-token-monitor")
        result = SyncResult(SyncStatus.NO_CHANGE, "code_pull", "代码仓库已是最新")
        find_project_root.return_value = project_root
        run_startup.return_value = result

        self.assertEqual(main(), 0)

        run_startup.assert_called_once_with(project_root)
        start_monitor.assert_called_once_with(["python.exe"], project_root, result)

    @patch("launcher.find_project_root", return_value=None)
    def test_main_reports_missing_project_without_sync(self, _find):
        with patch("launcher.show_error") as show_error, patch(
            "launcher.run_startup_code_sync"
        ) as run_startup:
            self.assertEqual(main(), 1)

        show_error.assert_called_once()
        run_startup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
