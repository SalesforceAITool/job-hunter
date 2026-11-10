"""Static Windows-readiness checks (we cannot run Windows here): launcher scripts, pythonw safety, encodings."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "install_windows.bat"
VBS = ROOT / "launch_windows.vbs"
PY_SOURCES = [ROOT / "app.py"] + sorted((ROOT / "jh").glob("*.py")) + [ROOT / "engine" / "jobhunt_mcp.py"] + \
    sorted((ROOT / "engine" / "resume").glob("*.py"))


def _text(p):
    return p.read_bytes().decode("ascii")  # .bat/.vbs must stay plain ASCII (cmd.exe code page)


class LauncherScriptTests(unittest.TestCase):
    def test_crlf_line_endings(self):
        for p in (BAT, VBS):
            raw = p.read_bytes()
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "%s has bare LF line endings" % p.name)

    def test_bat_python_discovery_order(self):
        t = _text(BAT)
        order = ["py -3", "python -c", r"%USERPROFILE%\anaconda3\python.exe", r"%USERPROFILE%\miniconda3\python.exe",
                 r"%LOCALAPPDATA%\anaconda3\python.exe", r"%LOCALAPPDATA%\miniconda3\python.exe",
                 r"%ProgramData%\anaconda3\python.exe", r"%ProgramData%\miniconda3\python.exe",
                 r"%LOCALAPPDATA%\Programs\Python\Python3*"]
        pos = [t.find(s) for s in order]
        self.assertTrue(all(p >= 0 for p in pos), dict(zip(order, pos)))
        self.assertEqual(pos, sorted(pos), "discovery order changed")
        self.assertIn("WindowsApps", t)                     # Microsoft Store stub rejected
        self.assertRegex(t, r'-c "import sys, ssl, sqlite3; assert sys.version_info >= \(3, 9\)"')
        self.assertIn(r"Library\bin", t)                    # Anaconda DLLs (ssl) when the env is not activated

    def test_bat_saves_interpreter_and_handles_missing_python(self):
        t = _text(BAT)
        self.assertIn(r'> "%APPDIR%\data\python_path.txt"', t)
        self.assertIn("https://www.python.org/downloads/", t)
        missing = t[t.find(":no_python"):]
        self.assertIn("pause", missing.split("exit /b 1")[0])

    def test_bat_reportlab_only_when_missing(self):
        t = _text(BAT)
        check = t.find('-c "import reportlab"')
        pip = t.find("-m pip install --user reportlab")
        self.assertTrue(0 <= check < pip, (check, pip))
        self.assertNotIn("--upgrade reportlab", t)

    def test_bat_quotes_paths_and_creates_shortcut(self):
        t = _text(BAT)
        self.assertIn('cd /d "%~dp0"', t)
        self.assertIn('"%PYEXE%"', t)
        for ln in t.splitlines():  # every use of the interpreter path is quoted (except the line saving it)
            if "%PYEXE%" in ln and "python_path.txt" not in ln:
                self.assertNotRegex(ln, r"(?<![\"'])%PYEXE%(?![\"'])", ln)
        self.assertIn("powershell -NoProfile -ExecutionPolicy Bypass", t)
        self.assertIn("CreateShortcut", t)
        self.assertIn("GetFolderPath('Desktop')", t)
        self.assertIn("IconLocation", t)
        self.assertIn("[char]34", t)                        # shortcut argument quoted: path may contain spaces
        self.assertIn("wscript.exe", t)

    def test_bat_no_unescaped_parens_in_block_echo(self):
        # an unescaped ")" inside echo within a ( ... ) block ends the block early
        depth = 0
        for ln in _text(BAT).splitlines():
            s = ln.strip()
            if depth and s.lower().startswith("echo"):
                self.assertNotRegex(s, r"(?<!\^)[()]", "unescaped paren in block: %s" % s)
            if s.lower().startswith(("rem", "echo")):
                continue
            depth += len(re.findall(r'\((?=[^"]*(?:"[^"]*"[^"]*)*$)', s)) - len(re.findall(r'\)(?=[^"]*(?:"[^"]*"[^"]*)*$)', s))

    def test_vbs_uses_saved_interpreter_and_pythonw(self):
        t = _text(VBS)
        self.assertIn(r'"\data\python_path.txt"', t)
        self.assertIn('\\pythonw.exe"', t)                 # pythonw.exe next to the saved python.exe
        self.assertIn(r"Library\bin", t)
        self.assertIn("sh.Run", t)
        self.assertRegex(t, r"sh\.Run c, 0, False")          # hidden window, don't wait
        self.assertIn('app = """" & dir & "\\app.py"""', t)  # app.py path quoted (spaces in folder names)
        self.assertIn("python.org", t)


class AppReuseTests(unittest.TestCase):
    def test_reuse_running_server(self):
        t = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("def existing_server", t)
        self.assertIn('"/api/summary"', t)
        self.assertIn('"/api/run", "POST"', t)
        self.assertIn("except runner.AlreadyRunning", t)
        self.assertIn("except OSError", t)                 # second double-click lost the bind race
        self.assertIn("MessageBoxW", t)


def _calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _name(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")


class PythonwSafetyTests(unittest.TestCase):
    def test_no_print_to_stdout(self):
        """pythonw has sys.stdout = None: print() without file=sys.stderr must not appear in app code."""
        bad = []
        for p in [ROOT / "app.py"] + sorted((ROOT / "jh").glob("*.py")):
            for c in _calls(p):
                if _name(c) == "print" and not any(k.arg == "file" for k in c.keywords):
                    bad.append("%s:%d" % (p.name, c.lineno))
        self.assertEqual(bad, [])

    def test_text_io_has_explicit_utf8(self):
        """Windows defaults to cp1252: every text-mode open()/read_text()/write_text() names encoding="utf-8"."""
        bad = []
        for p in PY_SOURCES:
            for c in _calls(p):
                n = _name(c)
                if n not in ("open", "read_text", "write_text"):
                    continue
                if isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) and \
                        c.func.value.id in ("os", "webbrowser", "urllib", "tarfile", "zipfile"):
                    continue
                mode = ""
                if n == "open":
                    if len(c.args) >= 2 and isinstance(c.args[1], ast.Constant):
                        mode = str(c.args[1].value)
                    for k in c.keywords:
                        if k.arg == "mode" and isinstance(k.value, ast.Constant):
                            mode = str(k.value.value)
                if "b" in mode:
                    continue
                if not any(k.arg == "encoding" for k in c.keywords):
                    bad.append("%s:%d %s" % (p.relative_to(ROOT), c.lineno, n))
        self.assertEqual(bad, [])

    def test_subprocesses_hide_console_windows(self):
        for p in (ROOT / "jh" / "engine.py", ROOT / "jh" / "resume.py", ROOT / "jh" / "envcheck.py"):
            t = p.read_text(encoding="utf-8")
            self.assertIn("0x08000000", t, p.name)       # CREATE_NO_WINDOW
            for c in _calls(p):
                for k in c.keywords:
                    if k.arg == "shell":
                        self.assertFalse(isinstance(k.value, ast.Constant) and k.value.value, "%s:%d" % (p.name, c.lineno))


if __name__ == "__main__":
    unittest.main()
