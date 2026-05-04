"""Self-update via `git pull --ff-only`.

When enabled, a background thread periodically checks the upstream branch
and runs `git pull` if there's a newer commit. After a successful update,
the process exits cleanly; systemd's Restart=always brings it back with the
new code (and any updated dependencies).

This module is safe by design:
- Disabled by default — caller (main.py) only starts the loop if the
  `auto_update_enabled` setting is set to "1".
- Uses --ff-only so any local divergence aborts the update instead of
  rewriting history.
- Skipping config: the loop reads settings on every tick, so toggling
  off in the UI takes effect on the next tick (no restart needed).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

DEFAULT_INTERVAL_HOURS = 24


def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 60) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                              capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def repo_dir() -> Path | None:
    """Walk up from this file to find a .git directory."""
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / ".git").exists():
            return p
        p = p.parent
    return None


def _commit_info(rev: str = "HEAD") -> dict | None:
    rdir = repo_dir()
    if not rdir:
        return None
    r = _run(["git", "log", "-1", "--format=%H%n%an%n%aI%n%s", rev], cwd=rdir)
    if not r or r.returncode != 0:
        return None
    parts = r.stdout.strip().split("\n", 3)
    if len(parts) < 4:
        return None
    return {"sha": parts[0], "short": parts[0][:7],
            "author": parts[1], "date": parts[2], "subject": parts[3]}


def local_commit_info() -> dict | None:
    return _commit_info("HEAD")


def remote_commit_info() -> dict | None:
    rdir = repo_dir()
    if not rdir:
        return None
    # Best-effort fetch (silent failure is fine — local info still useful).
    _run(["git", "fetch", "--quiet"], cwd=rdir, timeout=30)
    info = _commit_info("@{upstream}")
    if info:
        return info
    return _commit_info("origin/main")


def has_local_changes() -> bool:
    rdir = repo_dir()
    if not rdir:
        return False
    r = _run(["git", "status", "--porcelain"], cwd=rdir)
    return bool(r and r.stdout.strip())


def pull_now() -> tuple[bool, str, dict | None]:
    """Run `git pull --ff-only`. Returns (changed, message, new_local_info)."""
    rdir = repo_dir()
    if not rdir:
        return False, "no git repository found at install path", None
    before = local_commit_info()
    if has_local_changes():
        return False, "skipped: uncommitted local changes in repo", before
    r = _run(["git", "pull", "--ff-only"], cwd=rdir, timeout=120)
    if not r:
        return False, "git pull timed out", before
    if r.returncode != 0:
        return False, "git pull failed: " + ((r.stderr or r.stdout).strip() or "unknown error"), before
    after = local_commit_info()
    if before and after and before["sha"] != after["sha"]:
        # Best-effort: install any new requirements.
        venv_pip = rdir / ".venv" / "bin" / "pip"
        if venv_pip.exists():
            _run([str(venv_pip), "install", "-r", str(rdir / "requirements.txt")], timeout=180)
        return True, f"updated {before['short']} → {after['short']}", after
    return False, "already up to date", after


_loop_started = False
_lock = threading.Lock()


def start_background_loop(
    is_enabled: Callable[[], bool],
    interval_hours: Callable[[], float],
    on_update_about_to_restart: Callable[[], None] | None = None,
) -> None:
    """Spawn the watcher thread once. Safe to call multiple times."""
    global _loop_started
    with _lock:
        if _loop_started:
            return
        _loop_started = True

    def loop():
        # Wait one minute on boot before the first check so startup is calm.
        time.sleep(60)
        while True:
            try:
                interval = max(0.25, float(interval_hours()))   # min 15 min
            except Exception:
                interval = DEFAULT_INTERVAL_HOURS
            sleep_for = interval * 3600
            time.sleep(sleep_for)
            try:
                if not is_enabled():
                    continue
                changed, msg, _ = pull_now()
                print(f"[updater] {msg}", flush=True)
                if changed:
                    if on_update_about_to_restart:
                        try: on_update_about_to_restart()
                        except Exception: pass
                    print("[updater] exiting so systemd restarts with new code", flush=True)
                    os._exit(0)
            except Exception as e:
                print(f"[updater] error: {e}", flush=True)

    threading.Thread(target=loop, daemon=True, name="updater").start()
