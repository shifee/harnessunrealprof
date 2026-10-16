import json
import runpy
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class WatcherTests(unittest.TestCase):
    def run_startup(self, action, *, acknowledged=False, mismatch=False, initially_missing=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy2(ROOT / "templates/unreal-project/Content/Python/init_unreal.py", root / "init_unreal.py")
            shutil.copy2(ROOT / "unreal_harness/action_contract.py", root / "action_contract.py")
            (root / "execute_actions.py").write_text("from pathlib import Path\nPath(__file__).with_name('executed').write_text('yes')\n")
            args = {"path": "/Game/Test"} if action == "content.create_folder" else {}
            document = {"run_id": "pending", "commands": [{"id": "task", "action": action, "arguments": args}]}
            def publish():
                (root / "actions.json").write_text(json.dumps(document))
                (root / "harness.pending.json").write_text(json.dumps(dict(document, run_id="different") if mismatch else document))
            if not initially_missing:
                publish()
            if acknowledged:
                (root / "result.json").write_text(json.dumps({"run_id": "pending", "success": True}))
            fake = types.ModuleType("unreal")
            fake.log = lambda message: None
            fake.log_error = lambda message: None
            fake.register_slate_post_tick_callback = lambda callback: callback
            with patch.dict(sys.modules, {"unreal": fake}), patch("time.monotonic", side_effect=[0, 2, 4]):
                namespace = runpy.run_path(str(root / "init_unreal.py"))
                if initially_missing:
                    publish()
                namespace["watcher_tick"](1)
                namespace["watcher_tick"](1)
            return (root / "executed").exists()

    def test_unacknowledged_read_resumes_after_editor_start(self):
        self.assertTrue(self.run_startup("level.inspect"))

    def test_read_published_before_first_tick_is_executed(self):
        self.assertTrue(self.run_startup("level.inspect", initially_missing=True))

    def test_acknowledged_request_is_not_replayed(self):
        self.assertFalse(self.run_startup("level.inspect", acknowledged=True))

    def test_uncertain_mutation_is_not_replayed(self):
        self.assertFalse(self.run_startup("content.create_folder"))

    def test_mismatched_pending_marker_is_not_executed(self):
        self.assertFalse(self.run_startup("level.inspect", mismatch=True))


if __name__ == "__main__":
    unittest.main()
