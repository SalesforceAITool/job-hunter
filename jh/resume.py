"""Build a tailored resume PDF by running engine/resume/resume2.py in a subprocess.

The AI supplies {headline, summary, skills:[[label, text], ...], project_order:[keys]}.
Text is XML-escaped (reportlab Paragraph markup) and unknown project keys are dropped.
"""
import html
import re
import json
import os
import subprocess
import sys
from pathlib import Path

from . import config

PROJECT_KEYS = ["nissan", "asda", "scv", "amkor", "siemens", "bcbsm", "dubai", "emaar", "belden", "druce", "lifesci", "wm"]
ATTACHMENT_NAME = "Akshay_Verma_Resume.pdf"


class ResumeError(Exception):
    pass


def resume_script() -> Path:
    return config.app_root() / "engine" / "resume" / "resume2.py"


def python_for_subprocess() -> str:
    """Interpreter for child scripts; keeps pythonw on Windows so no console flashes."""
    return sys.executable or ("pythonw" if os.name == "nt" else "python3")


def pip_hint() -> str:
    """Command that installs reportlab for the interpreter the app really uses (Anaconda may have no `py`)."""
    exe = Path(python_for_subprocess())
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        exe = exe.with_name("python.exe")
    return '"%s" -m pip install --user reportlab' % exe


def no_window_flags() -> int:
    return 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


# engine/prompts/common.md "Skills he does NOT have" + no team-leading claims (Akshay never led/managed a team)
NOT_HIS = re.compile(
    r"\b(regrello|cg cloud|consumer goods cloud|ncino|communications cloud|vlocity cme|financial services cloud|"
    r"field service|\bfsl\b|marketing cloud|pardot|account engagement|mulesoft|tableau crm|crm analytics|veeva|"
    r"conga|apttus|servicenow|sap (?:development|abap)|abap|workday|oracle cpq|snowflake|java(?!\s*script)|\.net)\b"
    r"|\.net\b", re.I)
TEAM_CLAIM = re.compile(
    r"\b(?:led|lead(?:ing|s)?|manag(?:e|ed|es|ing)|head(?:ed|ing)?|supervis\w*|mentor(?:ed|ing)?|direct(?:ed|ing))\b"
    r"[^.;]{0,40}\b(?:team|developers|engineers|people|staff|reports)\b|\bteam of \d+|\b\d+[- ]?(?:member|person) team",
    re.I)


def _honest(text: str, split=r"(?<=[.!?])\s+") -> str:
    """Drop sentences / list items that claim a skill he lacks or leading a team."""
    parts = re.split(split, str(text or ""))
    keep = [p for p in parts if not (NOT_HIS.search(p) or TEAM_CLAIM.search(p))]
    sep = " " if split.startswith("(?<=") else ", "
    return sep.join(p.strip() for p in keep if p.strip())


def clean_config(resume: dict) -> dict:
    """Validate/sanitize the AI's resume dict into a resume2.py config (without 'out')."""
    if not isinstance(resume, dict):
        raise ResumeError("resume must be an object with headline, summary, skills, project_order")
    cfg = {}
    if resume.get("headline"):
        cfg["headline"] = _esc(_honest(resume["headline"], r"\s*[|,]\s*"))[:200]
    if resume.get("summary"):
        cfg["summary"] = _esc(_honest(resume["summary"]))[:2000]
    skills = resume.get("skills")
    if isinstance(skills, (list, tuple)) and skills:      # AI-written: anything else is ignored, never a crash
        rows = []
        for item in skills:
            if isinstance(item, dict):
                item = [item.get("label", ""), item.get("text", "")]
            if isinstance(item, (list, tuple)) and len(item) >= 2 and str(item[0]).strip():
                text = _honest(item[1], r"\s*[,;]\s*")
                if text and not NOT_HIS.search(str(item[0])):
                    rows.append([_esc(item[0])[:40], _esc(text)[:400]])
        if rows:
            cfg["skills"] = rows[:8]
    order = resume.get("project_order")
    if isinstance(order, (list, tuple)) and order:
        keys = []
        for k in order:
            k = str(k).strip().lower()
            if k in PROJECT_KEYS and k not in keys:
                keys.append(k)
        if keys:
            # keep every project; the AI's picks go first
            cfg["project_order"] = keys + [k for k in PROJECT_KEYS if k not in keys]
    return cfg


def build_pdf(resume: dict, out_path, timeout=120) -> str:
    """Write the PDF to out_path and return its path (str). Raises ResumeError."""
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = clean_config(resume or {})
    cfg["out"] = str(out)
    cfg_path = out.with_suffix(".json")
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    script = resume_script()
    if not script.exists():
        raise ResumeError("resume builder not found at %s" % script)
    if out.exists():
        try:
            out.unlink()
        except OSError as e:  # e.g. open in a PDF viewer on Windows
            raise ResumeError("cannot overwrite %s: %s" % (out, e))
    try:
        # utf-8 both ways: Windows defaults to cp1252, and a strict decode of the child's output would raise
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        try:
            from .engine import profile_email
            env["JH_PROFILE_EMAIL"] = profile_email()        # email printed on the resume = application email
        except Exception:
            pass
        proc = subprocess.run([python_for_subprocess(), str(script), str(cfg_path)], cwd=str(script.parent),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env, creationflags=no_window_flags())
    except subprocess.TimeoutExpired:
        raise ResumeError("resume build timed out after %ss" % timeout)
    except OSError as e:
        raise ResumeError("could not start resume builder: %s" % e)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        lines = [ln.strip() for ln in (proc.stderr or proc.stdout or "").strip().splitlines() if ln.strip()]
        if any("No module named 'reportlab'" in ln for ln in lines):
            raise ResumeError("reportlab is not installed for this Python - run install_windows.bat again "
                              "(or: %s)" % pip_hint())
        # the exception line (last) first: it is the useful part of a traceback
        detail = " | ".join(([lines[-1]] + lines[-5:-1]) if lines else [])
        raise ResumeError("exit %s: %s" % (proc.returncode, detail or "no output"))
    try:
        cfg_path.unlink()
    except OSError:
        pass
    return str(out)
