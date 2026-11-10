"""Environment checks for Settings -> Environment (each item: {id, label, ok, detail, fix})."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config


def _item(id, label, ok, detail="", fix=""):
    return {"id": id, "label": label, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix}


def _pip_hint():
    from .resume import pip_hint
    return pip_hint()


def _no_window():
    return 0x08000000 if os.name == "nt" else 0


def find_claude(cfg) -> str:
    """Path to the claude CLI: config engine.claude_path, PATH, then the native installer locations."""
    p = ((cfg or {}).get("engine") or {}).get("claude_path") or ""
    if p and Path(p).exists():
        return str(Path(p))
    found = shutil.which("claude")
    if found:
        return found
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    cands = [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
             home / ".claude" / "local" / "claude"]
    if os.environ.get("APPDATA"):
        cands.append(Path(os.environ["APPDATA"]) / "npm" / "claude.cmd")
    for c in cands:
        if c.exists():
            return str(c)
    return ""


def _run(argv, timeout=20):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          env=env, creationflags=_no_window(), stdin=subprocess.DEVNULL)


def _find_chrome() -> str:
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if p.exists():
                    return str(p)
        return ""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return shutil.which(name)
    mac = Path("/Applications/Google Chrome.app")
    return str(mac) if mac.exists() else ""


def run_checks(cfg=None, check_login=False) -> list:
    cfg = cfg or config.load()
    items = []
    v = sys.version_info
    items.append(_item("python", "Python 3.9+", v >= (3, 9), "Python %d.%d.%d (%s)" % (v[0], v[1], v[2], sys.executable),
                       "Install Python 3.11+ from python.org (tick 'Add python.exe to PATH')."))

    rl = importlib.util.find_spec("reportlab") is not None
    items.append(_item("reportlab", "reportlab (resume PDF)", rl, "installed" if rl else "not installed",
                       "Re-run install_windows.bat, or run: " + _pip_hint()))

    claude = find_claude(cfg)
    install_fix = ("Install Claude Code in PowerShell:  irm https://claude.ai/install.ps1 | iex   then run  claude  once "
                   "to log in. If it is installed elsewhere, set Settings > Engine > Claude path.")
    if not claude:
        items.append(_item("claude_cli", "Claude Code CLI", False, "claude not found on PATH or in ~/.local/bin",
                           install_fix))
        items.append(_item("claude_auth", "Claude login (subscription)", False, "claude CLI missing",
                           install_fix))
    else:
        try:
            r = _run([claude, "--version"], 20)
            ver = (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else ""
            items.append(_item("claude_cli", "Claude Code CLI", r.returncode == 0, "%s (%s)" % (ver or "?", claude),
                               install_fix))
        except (OSError, subprocess.TimeoutExpired) as e:
            items.append(_item("claude_cli", "Claude Code CLI", False, "%s: %s" % (claude, e), install_fix))
        items.append(_auth_item(claude))

    items.append(_engine_item())

    chrome = _find_chrome()
    items.append(_item("chrome", "Google Chrome", bool(chrome), chrome or "not found",
                       "Install Google Chrome, sign in to LinkedIn/JobRight there and add the Claude in Chrome extension."))

    if os.environ.get("ANTHROPIC_API_KEY"):
        items.append(_item("api_key_env", "No ANTHROPIC_API_KEY in environment", True,
                           "ANTHROPIC_API_KEY is set on this PC; the engine removes it for Claude runs so your "
                           "subscription is used."))

    configured = config.email_configured(cfg)
    em = cfg.get("email") or {}
    items.append(_item("gmail_configured", "Gmail configured", configured,
                       ("backend=file (test mode)" if em.get("backend") == "file" else em.get("address", "")) if configured
                       else "app password not set",
                       "Settings > Gmail: enter your address and a 16-letter Google App Password "
                       "(myaccount.google.com/apppasswords; needs 2-Step Verification)."))
    if check_login:
        from . import mailer
        ok, err = mailer.test_login(cfg) if configured else (False, "not configured")
        items.append(_item("gmail_login", "Gmail SMTP + IMAP login", ok, "login OK" if ok else (err or ""),
                           "Check the app password and that IMAP is enabled in Gmail settings."))

    try:
        d = config.data_dir()
        probe = d / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        items.append(_item("data_dir", "Data folder writable", True, str(d)))
    except OSError as e:
        items.append(_item("data_dir", "Data folder writable", False, str(e),
                           "Move the Job Hunter folder somewhere you can write (e.g. Documents) or set JOBHUNTER_DATA."))
    items.append(_item("dry_run", "Live sending", not cfg.get("dry_run"),
                       "dry run ON - emails are not sent" if cfg.get("dry_run") else "dry run OFF - emails are sent",
                       "After the email test passes, turn off 'Dry run' in Settings."))
    return items


def _auth_item(claude):
    fix = "Open a terminal and run  claude  then log in with your Claude subscription (/login)."
    try:
        r = _run([claude, "auth", "status"], 20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _item("claude_auth", "Claude login (subscription)", False, str(e), fix)
    out = (r.stdout or "").strip()
    info = {}
    try:
        info = json.loads(out[out.index("{"):]) if "{" in out else {}
    except ValueError:
        info = {}
    method = info.get("authMethod") or info.get("auth_method") or ""
    logged = info.get("loggedIn", info.get("logged_in", bool(method)))
    if not info:
        ok = r.returncode == 0 and "not logged" not in (out + r.stderr).lower()
        return _item("claude_auth", "Claude login (subscription)", ok, (out or r.stderr or "no output")[:200], fix)
    ok = bool(logged) and bool(method)
    detail = "authMethod: %s" % (method or "none")
    if ok and method != "claude.ai":
        detail += " (not the claude.ai subscription; runs may be billed to the API and Chrome may stay off)"
    if info.get("email"):
        detail += " - %s" % info["email"]
    return _item("claude_auth", "Claude login (subscription)", ok, detail, fix)


def _engine_item():
    p = config.app_root() / "jh" / "engine.py"
    mcp = config.app_root() / "engine" / "jobhunt_mcp.py"
    ok = p.exists() and mcp.exists()
    missing = [str(x.name) for x in (p, mcp) if not x.exists()]
    return _item("engine", "AI engine files", ok, "present" if ok else "missing: " + ", ".join(missing),
                 "Re-copy the full Job Hunter folder (jh/engine.py and engine/jobhunt_mcp.py are required for AI lanes).")
