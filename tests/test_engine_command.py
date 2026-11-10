import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jh import engine  # noqa: E402


class BuildCommandTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.data = Path(self.td.name) / "data"
        self.fake_claude = Path(self.td.name) / ("claude.exe" if os.name == "nt" else "claude")
        self.fake_claude.write_text("#!/bin/sh\n")
        self.fake_claude.chmod(0o755)
        self.cfg = {"engine": {"claude_path": str(self.fake_claude), "mock": False}}
        self.env_patch = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret", "ANTHROPIC_AUTH_TOKEN": "tok",
                                                      "CLAUDECODE": "1", "KEEP_ME": "yes"})
        self.env_patch.start()
        for k in ("JOBHUNTER_MOCK_ENGINE", "JOBHUNTER_DB", "JOBHUNTER_CONFIG"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env_patch.stop()
        self.td.cleanup()

    def build(self, cfg=None, **kw):
        return engine.build_command("jobright", cfg or self.cfg, engine.lane_prompt_path("jobright"), self.data, run_id=7, **kw)

    def test_env_scrubbed_and_set(self):
        argv, env = self.build()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("CLAUDECODE", env)
        self.assertEqual(env["KEEP_ME"], "yes")
        self.assertEqual(env["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
        self.assertEqual(env["JOBHUNTER_LANE"], "jobright")
        self.assertEqual(env["JOBHUNTER_RUN_ID"], "7")
        self.assertEqual(env["JOBHUNTER_DB"], str(self.data / "jobhunter.db"))
        self.assertEqual(env["JOBHUNTER_CONFIG"], str(self.data / "config.json"))
        # parent env untouched
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-secret")

    def test_argv_shape(self):
        argv, env = self.build()
        self.assertEqual(argv[0], str(self.fake_claude))
        self.assertEqual(argv[1], "-p")
        self.assertIn("jobright", argv[2])  # prompt right after -p, before variadic options
        self.assertFalse(argv[2].startswith("-"))
        for flag in ("--output-format", "--verbose", "--append-system-prompt-file", "--mcp-config", "--chrome",
                     "--strict-mcp-config", "--max-turns", "--allowedTools", "--permission-mode"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv.count("--mcp-config"), 1)
        hp = Path(argv[argv.index("--settings") + 1])                # browser guard hook (Akshay's Chrome only)
        self.assertIn("browser_guard.py", hp.read_text())
        i = argv.index("--allowedTools")
        self.assertEqual(argv[i + 1:i + 3], ["mcp__jobhunt__*", "mcp__claude-in-chrome__*"])

    def test_system_prompt_and_mcp_files(self):
        argv, env = self.build()
        sp = Path(argv[argv.index("--append-system-prompt-file") + 1])
        text = sp.read_text(encoding="utf-8")
        self.assertIn("Job Hunter engine — common rules", text)
        self.assertIn("Lane: JobRight", text)
        self.assertIn("Run id: 7", text)
        mp = Path(argv[argv.index("--mcp-config") + 1])
        cfg = json.loads(mp.read_text(encoding="utf-8"))
        srv = cfg["mcpServers"]["jobhunt"]
        self.assertEqual(srv["command"], sys.executable)
        self.assertTrue(srv["args"][0].endswith("jobhunt_mcp.py"))
        self.assertEqual(srv["env"]["JOBHUNTER_LANE"], "jobright")
        self.assertNotIn("ANTHROPIC_API_KEY", json.dumps(cfg))

    def test_config_args_override_and_max_turns(self):
        cfg = {"engine": {"claude_path": str(self.fake_claude),
                          "claude_args": ["--chrome", "--max-turns", "99", "--output-format", "json", "--verbose"]}}
        argv, _ = self.build(cfg, max_turns=4)
        self.assertEqual(argv[argv.index("--max-turns") + 1], "4")
        self.assertEqual(argv.count("--max-turns"), 1)
        self.assertEqual(argv.count("--output-format"), 1)
        self.assertEqual(argv.count("--verbose"), 1)
        self.assertNotIn("--strict-mcp-config", argv)

    def test_file_shell_web_tools_always_denied(self):
        """Regression (prompt injection): config.json written by an older version has only
        `--disallowedTools Agent`; Read/Bash/WebFetch must still be denied (engine cwd holds config.json)."""
        old = {"engine": {"claude_path": str(self.fake_claude),
                          "claude_args": ["--chrome", "--permission-mode", "dontAsk", "--allowedTools",
                                          "mcp__jobhunt__*", "mcp__claude-in-chrome__*", "--disallowedTools", "Agent",
                                          "--strict-mcp-config", "--max-turns", "150"]}}
        argv, _ = self.build(old)
        i = argv.index("--disallowedTools")
        j = next(k for k in range(i + 1, len(argv)) if argv[k].startswith("-"))
        denied = argv[i + 1:j]
        for t in ("Agent", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch"):
            self.assertIn(t, denied)
        self.assertEqual(argv.count("--disallowedTools"), 1)
        self.assertEqual(denied.count("Agent"), 1)
        self.assertIn("--strict-mcp-config", argv)
        # no flag at all in config -> added before --mcp-config, which stays last
        argv2, _ = self.build({"engine": {"claude_path": str(self.fake_claude), "claude_args": ["--chrome"]}})
        self.assertIn("Read", argv2[argv2.index("--disallowedTools") + 1:argv2.index("--mcp-config")])
        self.assertEqual(argv2[-2], "--mcp-config")
        self.assertEqual(argv2[2], engine.KICKOFF.format(lane="jobright"))

    def test_inline_system_prompt_mode(self):
        cfg = {"engine": {"claude_path": str(self.fake_claude), "system_prompt_mode": "inline"}}
        argv, _ = self.build(cfg)
        self.assertIn("--append-system-prompt", argv)
        self.assertNotIn("--append-system-prompt-file", argv)

    def test_mock_mode(self):
        argv, env = self.build({"engine": {"mock": True}})
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("mock_claude.py"))
        self.assertEqual(argv[2], "-p")
        with mock.patch.dict(os.environ, {"JOBHUNTER_MOCK_ENGINE": "1"}):
            argv2, _ = self.build({"engine": {}})
            self.assertTrue(argv2[1].endswith("mock_claude.py"))

    def test_claude_missing_raises(self):
        with mock.patch.object(engine.shutil, "which", return_value=None), \
             mock.patch.object(engine.Path, "home", return_value=Path(self.td.name) / "nohome"), \
             mock.patch.dict(os.environ, {"USERPROFILE": str(Path(self.td.name) / "nohome")}):
            self.assertIsNone(engine.resolve_claude({"engine": {"claude_path": ""}}))
            with self.assertRaises(FileNotFoundError):
                self.build({"engine": {}})

    def test_resolve_claude_fallback_userprofile(self):
        home = Path(self.td.name) / "home"
        exe = home / ".local" / "bin" / "claude.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("")
        with mock.patch.object(engine.shutil, "which", return_value=None), \
             mock.patch.dict(os.environ, {"USERPROFILE": str(home)}):
            self.assertEqual(engine.resolve_claude({}), str(exe))


class RegionPromptTest(unittest.TestCase):
    def _p(self, lane, regions):
        return engine.compose_system_prompt(lane, str(engine.PROMPTS_DIR / ("%s.md" % lane)), 1, "24h", regions)

    def test_regions_line_only_when_narrowed(self):
        t = self._p("linkedin_jobs", ["india"])
        self.assertIn("ONLY India remote", t)
        self.assertIn("Do not open the US remote, Worldwide remote search URLs", t)
        self.assertNotIn("Regions for this run", self._p("linkedin_jobs", ["us", "india", "world"]))
        self.assertNotIn("Regions for this run", self._p("linkedin_jobs", None))
        self.assertNotIn("Regions for this run", self._p("posts", ["us"]))  # posts have no regions
        self.assertIn("ONLY US remote", self._p("easy_apply", ["us"]))
        self.assertIn("Immediate joiner", self._p("easy_apply", ["us"]))  # answers bank still appended


if __name__ == "__main__":
    unittest.main()
