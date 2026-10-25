"""verify/hook_gate.py — the PostToolUse hook: runs the quick gate after a relevant write.

Registered in .claude/settings.json for Write|Edit|MultiEdit with the absolute MOS
interpreter.  Standard library only.  Reads the tool-use JSON on stdin and acts only for
paths under starbour\\, tests\\, or the files controls_config.ini and pyproject.toml
(relative to the repo root, which is this file's grandparent).  Everything else exits 0
silently (docs, specs, verify, .claude).

Exit codes (CONTRACTS C7):
  0  nothing to do, or the quick gate passed
  2  the quick gate failed — its output goes to stderr so the model reads it
  0 + HARNESS ERROR (as PostToolUse additionalContext JSON on stdout, mirrored to stderr)
     when the interpreter is wrong, the gate returned 3, or the 60 s timeout fired

Direct test (Phase 3 of docs/BOOTSTRAP.md):
  echo {"tool_name":"Write","tool_input":{"file_path":"E:\\\\CODE\\\\master-of-starbour\\\\starbour\\\\x.py"}} | python verify\\hook_gate.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

EXPECTED_PY = r"E:\CODE\master-of-starbour\MOS\Scripts\python.exe"
ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "verify" / "smoke_test.py"
WATCHED_DIRS = ("starbour", "tests")
WATCHED_FILES = ("controls_config.ini", "pyproject.toml")
GATE_TIMEOUT_S = 60


def harness_error(message: str) -> int:
    """Exit 0 but tell the model: a HARNESS ERROR is never the code's fault."""
    text = "HARNESS ERROR (hook_gate.py): " + message
    sys.stderr.write(text + "\n")
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}) + "\n")
    return 0


def watched_path(file_path: str) -> str | None:
    """The repo-relative POSIX path if the write is one the gate cares about, else None."""
    try:
        p = Path(file_path)
        if not p.is_absolute():
            p = ROOT / p
        rel = os.path.relpath(os.path.normcase(str(p.resolve(strict=False))), os.path.normcase(str(ROOT)))
    except (OSError, ValueError):
        return None
    if rel.startswith(".."):
        return None
    rel_posix = rel.replace("\\", "/")
    parts = rel_posix.split("/")
    if parts[0] in WATCHED_DIRS or rel_posix in WATCHED_FILES:
        return rel_posix
    return None


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        return 0
    rel = watched_path(str(file_path))
    if rel is None:
        return 0

    py = os.environ.get("MOS_PYTHON", EXPECTED_PY)
    if not Path(py).is_file() or "windowsapps" in py.lower() or not py.lower().replace("/", "\\").endswith(r"\mos\scripts\python.exe"):
        return harness_error(f"interpreter {py} is not the MOS venv ({EXPECTED_PY}); the quick gate did not run after writing {rel}")
    if "windowsapps" in sys.executable.lower() or not sys.executable.lower().replace("/", "\\").endswith(r"\mos\scripts\python.exe"):
        return harness_error(f"hook_gate.py is running under {sys.executable}, not the MOS venv — fix the command in .claude/settings.json")
    if not GATE.is_file():
        return harness_error(f"{GATE} not found")

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        cp = subprocess.run([py, str(GATE), str(ROOT), "--quick"], cwd=str(ROOT), env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=GATE_TIMEOUT_S,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return harness_error(f"the quick gate exceeded {GATE_TIMEOUT_S} s after writing {rel} — the loop may be hanging "
                             "(a sim that never ends, or a test without a timeout)")
    except OSError as e:
        return harness_error(f"could not start the quick gate: {e}")

    output = (cp.stdout or "") + (("\n" + cp.stderr) if cp.stderr else "")
    if cp.returncode == 0:
        sys.stdout.write(f"quick gate passed after writing {rel}\n")
        return 0
    if cp.returncode == 3:
        return harness_error(f"the quick gate reported HARNESS ERROR (exit 3) after writing {rel}:\n{output.strip()[-1500:]}")
    sys.stderr.write(f"QUICK GATE FAILED (exit {cp.returncode}) after writing {rel}. Fix the deliverable, never verify/.\n"
                     f"{output.strip()[-6000:]}\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — a crashing hook must still be loud and never block the tool
        sys.exit(harness_error(f"hook crashed: {type(e).__name__}: {e}"))
