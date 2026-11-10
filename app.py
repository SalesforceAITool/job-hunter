"""Job Hunter entry point (SPEC §12).

If a Job Hunter server already answers on the port: open the browser (and start a run if
auto_run_on_open and nothing is running). Otherwise start the server and open the browser
(and start a run only if auto_run_on_open is true; default false — Akshay picks lanes and clicks Run).
Logs go to data/logs/app.log.

    python app.py [--port N] [--no-browser] [--no-run]
"""
import argparse
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jh import config  # noqa: E402

log = logging.getLogger("jobhunter")


def setup_logging():
    logs = config.sub_dir("logs")
    handler = logging.handlers.RotatingFileHandler(str(logs / "app.log"), maxBytes=2_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # pythonw has no console: send stray prints/tracebacks to a file instead of crashing. Not app.log:
    # a second open handle on it makes RotatingFileHandler's rename fail on Windows.
    if sys.stdout is None or sys.stderr is None:
        stream = open(str(logs / "stdio.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    elif sys.stderr.isatty():
        root.addHandler(logging.StreamHandler(sys.stderr))


def _req(url, method="GET", timeout=2.0):
    req = urllib.request.Request(url, method=method, data=b"{}" if method == "POST" else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8") or "{}")


def existing_server(base):
    """Summary dict if our server already answers at base, else None."""
    try:
        status, data = _req(base + "/api/summary", timeout=1.5)
        if status == 200 and isinstance(data, dict) and "sent_total" in data:
            return data
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return None


def message_box(text, title="Job Hunter"):
    log.error(text)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
        except Exception:
            pass
    else:
        print(text, file=sys.stderr)


def keep_awake(on=True):
    """Windows: stop the PC from going to sleep while Job Hunter runs (Akshay, 10 Nov: "never sleep" so the
    2-hourly applies keep going). The screen can still turn off. Closing the app gives normal sleep back."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE_REQUIRED = 0x80000000, 0x00000001, 0x00000040
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED if on else 0)
        ok = bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
        log.info("keep awake %s: %s", "on" if on else "off", ok)
        return ok
    except Exception as e:
        log.warning("keep awake failed: %s", e)
        return False


BOOT_WINDOW_S = 15 * 60


def just_booted():
    """True in the first minutes after Windows started: the Startup shortcut launched us, nobody clicked."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0 < BOOT_WINDOW_S
    except Exception:
        return False


def open_job_hunter_chrome(cfg):
    """At boot, open the Job Hunter Chrome profile (engine.chrome_profile_dir) so the 2-hourly cycle has its
    browser. Only that profile; nothing when it is not set."""
    prof = str(((cfg.get("engine") or {}).get("chrome_profile_dir")) or "").strip()
    if not prof or os.name != "nt":
        return False
    import subprocess
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        exe = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
        if base and os.path.isfile(exe):
            try:
                subprocess.Popen([exe, "--profile-directory=" + prof, "https://www.linkedin.com/feed/"],
                                 close_fds=True)
                log.info("opened the Job Hunter Chrome (%s)", prof)
                return True
            except OSError as e:
                log.warning("could not open the Job Hunter Chrome: %s", e)
                return False
    log.warning("Chrome not found: Job Hunter Chrome not opened")
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Job Hunter")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-run", action="store_true", help="do not auto-start a run")
    args = ap.parse_args(argv)

    setup_logging()
    cfg = config.load()
    port = args.port or int(cfg.get("port", 8765))
    base = "http://127.0.0.1:%d" % port
    auto_run = bool(cfg.get("auto_run_on_open", False)) and not args.no_run
    booted = just_booted()
    if booted and not cfg.get("open_browser_at_boot", False):
        args.no_browser = True       # Akshay, 10 Nov: start quietly in the background when Windows starts
    log.info("starting Job Hunter (port %s, data %s)%s", port, config.data_dir(), " at Windows start" if booted else "")

    running = existing_server(base)
    if running is not None:
        log.info("server already running at %s - reusing it", base)
        if auto_run and not running.get("running"):
            try:
                _req(base + "/api/run", "POST", timeout=5)
            except urllib.error.HTTPError as e:
                log.info("run not started: HTTP %s", e.code)
            except (urllib.error.URLError, OSError) as e:
                log.warning("could not start run: %s", e)
        if not args.no_browser:
            webbrowser.open(base + "/")
        return 0

    from jh import runner, server
    try:
        srv = server.create_server(port)
    except OSError as e:
        if existing_server(base) is not None:  # a second double-click won the race: reuse that instance
            if not args.no_browser:
                webbrowser.open(base + "/")
            return 0
        message_box("Job Hunter could not start: port %d is in use by another program (%s).\n"
                    "Close that program or change the port in data/config.json." % (port, e))
        return 1
    server.serve_in_thread(srv)
    log.info("server listening on %s", base)
    if booted:
        open_job_hunter_chrome(cfg)
    if not args.no_browser:
        webbrowser.open(base + "/")

    if auto_run:
        def kick():
            time.sleep(2.0)  # let the page connect to the live stream first
            try:
                runner.start_run("open")
            except runner.AlreadyRunning:
                pass
            except Exception as e:
                log.exception("auto-run failed: %s", e)
        threading.Thread(target=kick, name="jh-autorun", daemon=True).start()

    if cfg.get("keep_awake", True):
        keep_awake(True)          # this main thread lives as long as the app, so the flag stays on until it exits
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("shutting down")
        keep_awake(False)
        runner.stop_run()
        server.shutdown_server(srv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
