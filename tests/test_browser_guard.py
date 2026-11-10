"""Hard guard: Job Hunter may only drive Akshay's own Chrome on this laptop (Akshay, 29 Oct)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))
import browser_guard as g  # noqa: E402

DEV = "3b7d6d57-059f-4afc-aa29-2371bfb387e2"
OTHER = "84f268b1-acab-4492-b586-bbdde90ebdaa"


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.d, "config.json")

    def cfg(self, dev=DEV):
        return {"engine": {"chrome_device_id": dev}}

    def ev(self, tool, sid="s1", **ti):
        return {"session_id": sid, "tool_name": "mcp__claude-in-chrome__" + tool, "tool_input": ti}

    def test_other_browser_blocked_locked_one_allowed(self):
        self.assertFalse(g.decide(self.ev("select_browser", deviceId=OTHER), self.cfg_path, self.cfg())[0])
        self.assertFalse(g.decide(self.ev("navigate", url="https://www.linkedin.com/"), self.cfg_path, self.cfg())[0])
        self.assertTrue(g.decide(self.ev("list_connected_browsers"), self.cfg_path, self.cfg())[0])
        self.assertTrue(g.decide(self.ev("select_browser", deviceId=DEV), self.cfg_path, self.cfg())[0])
        self.assertTrue(g.decide(self.ev("navigate", url="https://www.linkedin.com/"), self.cfg_path, self.cfg())[0])
        # another session (another lane) must select first too
        self.assertFalse(g.decide(self.ev("navigate", sid="s2"), self.cfg_path, self.cfg())[0])
        # switching away later in the same session is blocked
        self.assertFalse(g.decide(self.ev("select_browser", deviceId=OTHER), self.cfg_path, self.cfg())[0])

    def test_switch_browser_always_blocked_and_unlocked_first_run_free(self):
        self.assertFalse(g.decide(self.ev("switch_browser"), self.cfg_path, self.cfg())[0])
        self.assertFalse(g.decide(self.ev("switch_browser"), self.cfg_path, self.cfg(""))[0])
        self.assertTrue(g.decide(self.ev("select_browser", deviceId=OTHER), self.cfg_path, self.cfg(""))[0])
        self.assertTrue(g.decide({"tool_name": "mcp__jobhunt__save_job"}, self.cfg_path, self.cfg())[0])

    def test_script_exit_code_2_blocks(self):
        with open(self.cfg_path, "w") as f:
            json.dump(self.cfg(), f)
        env = dict(os.environ, JOBHUNTER_CONFIG=self.cfg_path)
        r = subprocess.run([sys.executable, str(ROOT / "engine" / "browser_guard.py")],
                           input=json.dumps(self.ev("select_browser", deviceId=OTHER)), env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn(DEV, r.stderr)
        r = subprocess.run([sys.executable, str(ROOT / "engine" / "browser_guard.py")],
                           input=json.dumps(self.ev("select_browser", deviceId=DEV)), env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)

    def test_engine_passes_hooks_file(self):
        from jh import engine
        p = engine.write_browser_guard(Path(self.d), "posts", "C:\\Python314\\pythonw.exe")
        doc = json.loads(Path(p).read_text())
        h = doc["hooks"]["PreToolUse"][0]
        self.assertEqual(h["matcher"], "^mcp__claude-in-chrome__.*$")
        self.assertIn("C:/Python314/pythonw.exe", h["hooks"][0]["command"])
        self.assertIn("browser_guard.py", h["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
