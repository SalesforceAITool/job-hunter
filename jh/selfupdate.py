"""Self-restart on a new build (27 Nov: "consider this laptop as a server ... improve daily").

Fixes are copied onto the laptop while Job Hunter runs. Prompts, the MCP tool server and the resume builder are read
fresh by every run, but the app process itself (watcher, outbox, server) keeps the code it started with — on 26/27 Nov
the page kept saying "restart to load the update" and double-clicking the icon only reused the old process.

Now the watcher compares the BUILD in jh/__init__.py on disk with the one it loaded, once a minute. When they differ
and no run is going, it checks that the new code imports cleanly (a separate python process), starts a fresh copy of
the app that waits for the port to be free (app.py --takeover), and exits. A new build that does not import is
never started: the old process keeps running and says why.
"""
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from . import BUILD

log = logging.getLogger("jobhunter.selfupdate")
ROOT = Path(__file__).resolve().parent.parent
_BUILD_RX = re.compile(r'^BUILD\s*=\s*["\']([^"\']+)["\']', re.M)


def disk_build(root=None):
    try:
        text = (Path(root or ROOT) / "jh" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return None
    m = _BUILD_RX.search(text)
    return m.group(1) if m else None


def pending(root=None):
    """The build on disk when it differs from the running one, else None."""
    b = disk_build(root)
    return b if b and b != BUILD else None


def imports_ok(py=None, root=None, timeout=90):
    """(ok, error): the code on disk imports in a fresh interpreter (catches a half-copied or broken update)."""
    py = py or sys.executable
    code = "import jh, jh.server, jh.watch, jh.runner, jh.engine, jh.outbox, jh.followups, jh.actions, jh.report"
    try:
        r = subprocess.run([py, "-c", code], cwd=str(root or ROOT), capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "exit %s" % r.returncode).strip()[-600:]
    return True, None


def spawn_takeover(py=None, root=None):
    """Start `app.py --takeover --no-browser` detached; it waits for this process to free the port."""
    py = py or sys.executable
    argv = [py, str(Path(root or ROOT) / "app.py"), "--takeover", "--no-browser"]
    kw = {"cwd": str(root or ROOT), "close_fds": True, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(argv, **kw)


def restart(exit_fn=None, py=None, root=None):
    """Check, spawn the new process, exit this one. -> (False, why) if the new build is not started."""
    new = pending(root)
    if not new:
        return False, "no new build"
    ok, err = imports_ok(py, root)
    if not ok:
        return False, "new build %s does not import: %s" % (new, err)
    spawn_takeover(py, root)
    log.info("restarting: running %s, new build %s", BUILD, new)
    (exit_fn or os._exit)(0)
    return True, None
