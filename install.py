#!/usr/bin/env python3
"""Install the inko-handwriting skill for your AI agent (Claude Code, Codex, Cursor, Copilot, Gemini CLI, OpenCode …).

    python install.py                        # detect the agent, install into the current project
    python install.py --scope user           # install once for all projects
    python install.py --agent claude         # or: agents (Codex, Cursor, Copilot, Gemini CLI, OpenCode, goose, Amp) / both
    python install.py --project path/to/project --dry-run

Where skills live:
    Claude Code                                     .claude/skills/   and  ~/.claude/skills/
    Codex, Cursor, GitHub Copilot, Gemini CLI,       .agents/skills/   and  ~/.agents/skills/
    OpenCode, goose, Amp

What it does: copies inko-handwriting/ there, installs Pillow + numpy if missing (pip), runs a health check, and prints
JSON with the installed path and the next steps. It never asks for, prints or stores your API key — that's
`scripts/inko.py auth`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE / "inko-handwriting"
NAME = "inko-handwriting"


def detect_agent() -> tuple[str, str]:
    env = os.environ
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude", "running inside Claude Code"
    if any(k.startswith("CODEX_") for k in env):
        return "agents", "running inside Codex"
    if env.get("GEMINI_CLI"):
        return "agents", "running inside Gemini CLI"
    if env.get("CURSOR_TRACE_ID") or "cursor" in env.get("TERM_PROGRAM", "").lower():
        return "agents", "running inside Cursor"
    if env.get("OPENCODE") or env.get("OPENCODE_BIN"):
        return "agents", "running inside OpenCode"
    return "", "agent not detected"


def targets(agent: str, scope: str, project: Path) -> list[Path]:
    base = Path.home() if scope == "user" else project
    dirs = {"claude": [base / ".claude" / "skills"], "agents": [base / ".agents" / "skills"],
            "both": [base / ".claude" / "skills", base / ".agents" / "skills"]}[agent]
    return [d / NAME for d in dirs]


def copy_skill(dst: Path, link: bool) -> None:
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    elif dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if link:
        dst.symlink_to(SKILL, target_is_directory=True)
    else:
        shutil.copytree(SKILL, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))


def have_deps() -> dict:
    out = {}
    for mod, pkg in (("PIL", "pillow"), ("numpy", "numpy")):
        try:
            __import__(mod)
            out[pkg] = True
        except ImportError:
            out[pkg] = False
    return out


def pip_install(req: Path) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and "Permission" in (r.stderr + r.stdout) and not os.environ.get("VIRTUAL_ENV"):
        r = subprocess.run(cmd + ["--user"], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout)[-600:]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", choices=["auto", "claude", "agents", "both"], default="auto")
    ap.add_argument("--scope", choices=["project", "user"], default="project")
    ap.add_argument("--project", default=".", help="project folder (default: current directory)")
    ap.add_argument("--no-deps", action="store_true", help="don't pip-install Pillow/numpy")
    ap.add_argument("--link", action="store_true", help="symlink instead of copy (for developing the skill)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if sys.version_info < (3, 9):
        sys.exit("error: Python 3.9 or newer is needed")
    if not (SKILL / "SKILL.md").exists():
        sys.exit(f"error: {SKILL} is missing — run install.py from inside the downloaded repository")
    agent, why = (a.agent, "chosen with --agent") if a.agent != "auto" else detect_agent()
    if not agent:
        project = Path(a.project).resolve()
        agent = "claude" if (project / ".claude").is_dir() else "agents" if (project / ".agents").is_dir() else "both"
        why = {"claude": "found .claude/ in the project", "agents": "found .agents/ in the project",
               "both": "agent unknown: installing for Claude Code and for .agents-compatible agents"}[agent]
    dsts = targets(agent, a.scope, Path(a.project).resolve())
    result: dict = {"agent": agent, "why": why, "scope": a.scope, "installed_to": [str(d) for d in dsts], "dry_run": a.dry_run}
    if a.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return
    for d in dsts:
        copy_skill(d, a.link)
    deps = have_deps()
    if not all(deps.values()) and not a.no_deps:
        ok, log = pip_install(SKILL / "scripts" / "requirements.txt")
        deps = have_deps()
        if not ok:
            result["pip_error"] = log
    result["python_deps"] = deps
    doctor = subprocess.run([sys.executable, str(dsts[0] / "scripts" / "inko.py"), "doctor"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    try:
        result["doctor"] = json.loads(doctor.stdout)
    except ValueError:
        result["doctor"] = (doctor.stdout or doctor.stderr)[-800:]
    inko = dsts[0] / "scripts" / "inko.py"
    steps = []
    if not all(deps.values()):
        steps.append(f"{sys.executable} -m pip install -r \"{dsts[0] / 'scripts' / 'requirements.txt'}\"")
    key_ok = isinstance(result["doctor"], dict) and str(result["doctor"].get("key", "")).startswith("valid")
    if not key_ok:
        steps.append(f"Save the user's API key without echoing it: pipe it into  python \"{inko}\" auth   "
                     "(add --project to keep it in ./.inko/key instead of the user config folder)")
    steps.append(f"Read {dsts[0] / 'SKILL.md'} now and follow it; agents list new skills in a new session at the latest.")
    result["next"] = steps
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if sys.platform == "win32":
        for st in (sys.stdout, sys.stderr):
            try:
                st.reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
    main()
