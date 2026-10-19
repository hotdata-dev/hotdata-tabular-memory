"""Is this machine ready, and what does a new user need to hear first.

The session-start hook asks once per machine, with one `hotdata auth status` call. When
the answer is yes it records that and stays quiet from then on; until then each session
says the one thing to fix. `tabular-memory doctor` runs every check, every time.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import hotdata
import project

INSTALL = "curl -fsSL https://hotdata.dev/install | sh"
LOGIN = "hotdata auth login"
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist",
             "build", "target", ".cache"}


def marker() -> Path:
    return project.data_dir() / "ready"


def installed() -> bool:
    return shutil.which("hotdata") is not None


def logged_in(timeout: int = 5) -> tuple[bool, str]:
    """Return (logged in, what the CLI said). Never raises."""
    try:
        done = subprocess.run(["hotdata", "auth", "status", "--no-input"],
                              capture_output=True, text=True, timeout=timeout,
                              env=hotdata.credentials())
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    text = hotdata._plain(done.stdout + done.stderr)
    m = re.search(r"Authenticated:\s*(\w+)", text)
    return bool(m and m.group(1).lower() == "yes"), text


def problem() -> str:
    """Return the one thing stopping the plugin from working, or "" when nothing is."""
    if not installed():
        return f"the `hotdata` CLI is not installed. Install it with `{INSTALL}`, then run `{LOGIN}`."
    ok, _ = logged_in()
    if not ok:
        return f"the `hotdata` CLI is not logged in. Type `! {LOGIN}` (it opens a browser)."
    return ""


def session_check() -> tuple[str, str]:
    """Return (problem, welcome) for the session-start hook; at most one is set.

    A machine that passed once is not asked again, so a ready session costs one
    PATH lookup. A failure later surfaces as the command's own error.
    """
    if marker().exists():
        return ("" if installed() else problem()), ""
    found = problem()
    if found:
        return found, ""
    marker().write_text("ok\n", encoding="utf-8")
    return "", WELCOME


WELCOME = ("Hotdata Tabular Memory is ready. Data files too big for context now go to "
           "Hotdata, and Claude queries them there. Try it: ask Claude to run "
           "`tabular-memory demo`. Check setup any time with `tabular-memory doctor`.")


# ------------------------------------------------------------------------ doctor


def checks(cwd: str) -> list[tuple[bool, str, str]]:
    """Return `[(ok, what, fix)]` for every check, stopping where later ones cannot run."""
    out: list[tuple[bool, str, str]] = []
    if not installed():
        return [(False, "hotdata CLI not found on PATH", INSTALL)]
    try:
        version = subprocess.run(["hotdata", "--version"], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        version = "hotdata"
    out.append((True, f"{version} installed", ""))
    if not logged_in()[0]:
        out.append((False, "not logged in", f"! {LOGIN}"))
        return out
    out.append((True, "logged in", ""))
    try:
        workspace = project.workspace_id()
    except hotdata.HotdataError:
        workspace = ""
    out.append((bool(workspace), f"workspace {workspace or 'not found'}",
                "" if workspace else "hotdata workspaces use"))
    if not workspace:
        return out
    label = project.project_label(cwd)
    try:
        entry = project.project_database(cwd)
    except hotdata.HotdataError as error:
        out.append((False, f"project {label}: database unreadable ({error})",
                    "check the `database` plugin option"))
        return out
    if not entry:
        out.append((True, f"project {label}: no database yet",
                    "tabular-memory load <file>  (or tabular-memory demo)"))
        return out
    try:
        names = hotdata.table_names(entry["database"], entry["catalog"])
        out.append((True, f"project {label}: database {entry['database']}, "
                          f"{len(names)} table{'s' * (len(names) != 1)}", ""))
    except hotdata.HotdataError as error:
        out.append((False, f"project {label}: database {entry['database']} unreachable ({error})",
                    "check the workspace, or the `database` plugin option"))
    guard = project.option("read_guard", "on")
    out.append((True, f"large-file guard {guard}, "
                      f"{project.option('guard_threshold_mb', '8')}MB", ""))
    return out


# --------------------------------------------------------------- unloaded files


def unloaded_files(cwd: str, threshold: int, limit: int = 5,
                   max_entries: int = 5000, depth: int = 4) -> list[tuple[Path, int]]:
    """Return up to `limit` data files over `threshold` bytes under the project root.

    Shallow and bounded, because it runs at session start: at most `depth` levels and
    `max_entries` directory entries, skipping dependency and build folders.
    """
    root = Path(cwd or os.getcwd()).resolve()
    for folder in (root, *root.parents):
        if (folder / ".git").exists():
            root = folder
            break
    found: list[tuple[Path, int]] = []
    seen = 0
    stack = [(root, 0)]
    while stack and seen < max_entries:
        folder, level = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for item in entries:
            seen += 1
            try:
                if item.is_dir(follow_symlinks=False):
                    if level < depth and item.name not in SKIP_DIRS and not item.name.startswith("."):
                        stack.append((Path(item.path), level + 1))
                elif Path(item.name).suffix.lower() in project.DATA_EXTENSIONS:
                    size = item.stat().st_size
                    if size > threshold:
                        found.append((Path(item.path), size))
            except OSError:
                continue
    found.sort(key=lambda f: -f[1])
    return [(p.relative_to(root) if p.is_relative_to(root) else p, s) for p, s in found[:limit]]


def unloaded_hint(cwd: str, threshold: int) -> str:
    files = unloaded_files(cwd, threshold)
    if not files:
        return ""
    lines = ["This project has data files too big to read into context, not yet in Hotdata:"]
    lines += [f"  tabular-memory load {p}    # {s / (1024 * 1024):.0f}MB" for p, s in files]
    lines.append("Load one before answering questions about it; the load prints its schema "
                 "and a sample, and every question after that is a `tabular-memory query`.")
    return "\n".join(lines)
