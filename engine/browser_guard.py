"""Claude Code PreToolUse hook: Job Hunter may drive ONLY Akshay's own Chrome on this laptop (Akshay, 29 Oct).

The engine starts every lane with --settings pointing at a hooks file that runs this script before each
mcp__claude-in-chrome__* tool call. With engine.chrome_device_id set in config.json:
  * select_browser with any other deviceId           -> blocked
  * switch_browser (pairing prompt to every Chrome)  -> blocked
  * any other Chrome tool before select_browser(locked id) in this session -> blocked
Exit code 2 = Claude Code blocks the call and shows our stderr to the model. Without a lock (first run) only
switch_browser is blocked; the lane then locks to the Chrome on this computer signed in as Akshay.
"""
import json
import os
import sys
import time

FREE = ("list_connected_browsers",)


def _config():
    p = os.environ.get("JOBHUNTER_CONFIG") or ""
    try:
        with open(p, encoding="utf-8") as f:
            return p, json.load(f)
    except Exception:
        return p, {}


def _marker_dir(cfg_path):
    d = os.path.join(os.path.dirname(cfg_path) or ".", "engine", "guard")
    os.makedirs(d, exist_ok=True)
    return d


def _clean(d):
    now = time.time()
    for n in os.listdir(d):
        try:
            if now - os.path.getmtime(os.path.join(d, n)) > 86400:
                os.remove(os.path.join(d, n))
        except OSError:
            pass


def decide(event, cfg_path, cfg):
    """(allowed, message) for one PreToolUse event."""
    tool = str(event.get("tool_name") or "")
    if not tool.startswith("mcp__claude-in-chrome__"):
        return True, ""
    short = tool[len("mcp__claude-in-chrome__"):]
    ti = event.get("tool_input") or {}
    locked = str(((cfg.get("engine") or {}).get("chrome_device_id")) or "").strip()
    other = [str(x) for x in ((cfg.get("engine") or {}).get("exclude_device_ids") or []) if x]
    who = str((cfg.get("profile") or {}).get("name") or "Akshay")
    if short == "switch_browser":
        return False, ("Blocked by Job Hunter: switch_browser sends a pairing prompt to every Chrome (other laptops,"
                       " other people). Use select_browser with %s's deviceId." % who)
    if short == "select_browser":
        dev0 = str(ti.get("deviceId") or ti.get("device_id") or "").strip()
        if dev0 and dev0 in other and dev0 != locked:
            return False, ("Blocked by Job Hunter: that Chrome belongs to another profile (not %s). Use only %s's own"
                           " Chrome." % (who, who))
    if not locked or short in FREE:
        return True, ""
    session = "".join(ch for ch in str(event.get("session_id") or "nosession") if ch.isalnum() or ch in "-_")[:80]
    mark = os.path.join(_marker_dir(cfg_path), session or "nosession")
    if short == "select_browser":
        dev = str(ti.get("deviceId") or ti.get("device_id") or "").strip()
        if dev != locked:
            return False, ("Blocked by Job Hunter: only %s's own Chrome on this laptop may be used (deviceId %s)."
                           " Call select_browser with exactly that deviceId; never any other browser." % (who, locked))
        with open(mark, "w", encoding="utf-8") as f:
            f.write(locked)
        return True, ""
    try:
        with open(mark, encoding="utf-8") as f:
            ok = f.read().strip() == locked
    except OSError:
        ok = False
    if not ok:
        return False, ("Blocked by Job Hunter: first call select_browser with deviceId %s (%s's Chrome on this"
                       " laptop). No other browser may be used." % (locked, who))
    return True, ""


def main():
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    cfg_path, cfg = _config()
    try:
        _clean(_marker_dir(cfg_path))
    except OSError:
        pass
    ok, msg = decide(event, cfg_path, cfg)
    if not ok:
        sys.stderr.write(msg + "\n")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
