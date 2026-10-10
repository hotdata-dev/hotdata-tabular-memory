"""What the plugin must do: keep methods, refuse conclusions, and never block."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bridge  # noqa: E402
import capture  # noqa: E402
from store import FileStore, Record, figures, reads_as_a_conclusion  # noqa: E402

NOTE = """---
name: prefers-short-answers
description: Eddie wants terse, technically accurate explanations
metadata:
  type: feedback
---

Keep explanations short. **Why:** long answers bury the decision.
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Clear ambient configuration: a developer with these exported would otherwise
    # see failures that have nothing to do with the code.
    for name in list(os.environ):
        if name.startswith("CLAUDE_PLUGIN_OPTION_") or name == "HOTDATA_MEMORY_PROJECT":
            monkeypatch.delenv(name, raising=False)
    projects = tmp_path / "projects"
    (projects / "a-project" / "memory").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MEMORY_FILE", str(tmp_path / "mem.json"))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MEMORY_ROOT", str(projects))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SCOPE_PREFIX", "claude")
    return projects


def cli(args, extra=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "memcli.py"), *args],
        capture_output=True, text=True,
        env={**os.environ, **(extra or {}), "PYTHONPATH": str(SCRIPTS)})


def hook(name, payload, extra=None):
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, **(extra or {}), "PYTHONPATH": str(SCRIPTS)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else {}


# ------------------------------------------------------------------ the record


def test_the_same_fact_written_twice_is_stored_once(env):
    store = FileStore(Path(os.environ["CLAUDE_PLUGIN_OPTION_MEMORY_FILE"]))
    one = Record(namespace="claude/t", subject="dialect", kind="fact",
                 content="date_diff does not exist here")
    two = Record(namespace="claude/t", subject="dialect", kind="fact",
                 content="date_diff   does not EXIST here")
    assert one.id == two.id
    store.write([one])
    store.write([two])
    assert len(store.current("claude/t")) == 1


def test_a_superseded_record_leaves_the_current_set_but_not_the_history(env):
    store = FileStore(Path(os.environ["CLAUDE_PLUGIN_OPTION_MEMORY_FILE"]))
    old = Record(namespace="claude/t", subject="clean-set", kind="fact",
                 content="keep rows where fare_amount > 0")
    store.write([old])
    new = Record(namespace="claude/t", subject="clean-set", kind="fact",
                 content="keep rows where fare_amount > 0 and distance > 0",
                 supersedes=old.id)
    store.write([new])
    current = store.current("claude/t")
    assert [r.id for r in current] == [new.id]
    assert len(store._load()) == 2


@pytest.mark.parametrize(("content", "conclusion"), [
    ("clean set 2869585 rows, mean fare 17.49, share 0.8009", True),
    ("Counts: 38341 zero fares, 41391 tips over fare, 60371 zero distance", True),
    ("Fare per mile is 17.49 and median duration 8.37 minutes", False),
    ("date_diff does not exist; use EXTRACT(EPOCH FROM (a - b))", False),
    ("DataFusion 55 has no date_diff", False),
])
def test_several_figures_read_as_a_conclusion(content, conclusion):
    assert reads_as_a_conclusion(content) is conclusion


def test_a_version_number_is_not_a_result():
    assert figures("DataFusion 55 has no date_diff") == []


# ------------------------------------------------------------------- the checks


def test_figures_without_a_query_are_refused(env):
    out = cli(["--scope", "claude/t", "remember",
               "clean set 2869585 rows, mean fare 17.49, share 0.8009",
               "--subject", "clean-set"])
    assert out.returncode == 2
    assert "no query to reproduce them" in out.stderr


def test_figures_with_a_query_are_kept(env):
    out = cli(["--scope", "claude/t", "remember",
               "clean set 2869585 rows, mean fare 17.49, share 0.8009",
               "--subject", "clean-set", "--query", "SELECT count(*) FROM trips"])
    assert out.returncode == 0, out.stderr
    record = bridge.store().current("claude/t")[0]
    assert record.query == "SELECT count(*) FROM trips"


def test_a_second_record_on_one_subject_is_refused_however_worded(env):
    first = ["--scope", "claude/t", "remember",
             "Clean set: pickup in January, dropoff after pickup, distance above zero",
             "--subject", "clean-set"]
    assert cli(first).returncode == 0
    second = ["--scope", "claude/t", "remember",
              "Benchmark clean set: fare_amount positive and total_amount positive",
              "--subject", "clean-set"]
    out = cli(second)
    assert out.returncode == 2
    assert "says something different" in out.stderr
    assert "memory supersede" in out.stderr
    assert cli([*second, "--force"]).returncode == 0


def test_a_different_subject_is_not_a_clash(env):
    assert cli(["--scope", "claude/t", "remember", "Engine is DataFusion",
                "--subject", "dialect"]).returncode == 0
    assert cli(["--scope", "claude/t", "remember", "Zones has 265 rows",
                "--subject", "zones"]).returncode == 0


def test_supersede_replaces_the_record_the_check_pointed_at(env):
    cli(["--scope", "claude/t", "remember", "Clean set keeps positive fares",
         "--subject", "clean-set"])
    old = bridge.store().current("claude/t")[0]
    out = cli(["--scope", "claude/t", "supersede", old.id,
               "Clean set keeps positive fares and positive distance"])
    assert out.returncode == 0, out.stderr
    current = bridge.store().current("claude/t")
    assert len(current) == 1
    assert "positive distance" in current[0].content


# -------------------------------------------------------------------- the scope


def test_a_repository_names_the_project_from_any_subdirectory(env, tmp_path):
    repo = tmp_path / "my-repo"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "services" / "api"
    deep.mkdir(parents=True)
    assert bridge.namespace_for(str(repo)) == "claude/my-repo"
    assert bridge.namespace_for(str(deep)) == "claude/my-repo"


def test_the_project_option_wins_over_the_directory(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_PROJECT", "pinned")
    assert bridge.namespace_for("/anywhere/at/all") == "claude/pinned"


# --------------------------------------------------------------------- the hooks


def test_a_written_note_comes_back_at_the_next_session_start(env):
    path = env / "a-project" / "memory" / "prefers-short-answers.md"
    path.write_text(NOTE, encoding="utf-8")
    cwd = "/x/a-project"
    assert hook("capture", {"tool_input": {"file_path": str(path)}, "cwd": cwd}) == {}
    out = hook("session_start", {"cwd": cwd})
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "Eddie wants terse" in context
    assert "memory remember" in context


def test_the_index_and_other_files_are_not_notes(env):
    memory = env / "a-project" / "memory"
    assert capture.is_memory_file(memory / "note.md")
    assert not capture.is_memory_file(memory / "MEMORY.md")
    assert not capture.is_memory_file(memory / "note.txt")


def test_a_session_with_no_memory_still_says_how_to_write(env):
    context = hook("session_start", {"cwd": "/x/b-project"}
                   )["hookSpecificOutput"]["additionalContext"]
    assert "No memory is recorded" in context
    assert "memory remember" in context


def test_a_store_that_cannot_be_reached_does_not_stop_a_session(env, monkeypatch):
    out = hook("session_start", {"cwd": "/x/never-seen"},
               extra={"CLAUDE_PLUGIN_OPTION_DATABASE": "dbidnope",
                      "HOTDATA_API_KEY": "none"})
    assert "hookSpecificOutput" in out or "systemMessage" in out


def test_a_store_that_cannot_be_reached_does_not_block_an_edit(env):
    path = env / "a-project" / "memory" / "n.md"
    path.write_text(NOTE, encoding="utf-8")
    out = hook("capture", {"tool_input": {"file_path": str(path)}, "cwd": "/x/a"},
               extra={"CLAUDE_PLUGIN_OPTION_DATABASE": "dbidnope",
                      "HOTDATA_API_KEY": "none"})
    assert out == {} or "systemMessage" in out


def test_forget_removes_the_record_and_revives_what_it_replaced(env):
    """Deleting a correction is destructive in both directions — hence the warning."""
    cli(["--scope", "claude/t", "remember", "Clean set keeps positive fares",
         "--subject", "clean-set"])
    old = bridge.store().current("claude/t")[0]
    cli(["--scope", "claude/t", "supersede", old.id,
         "Clean set keeps positive fares and positive distance"])
    new = bridge.store().current("claude/t")[0]
    assert new.id != old.id
    out = cli(["--scope", "claude/t", "forget", new.id])
    assert out.returncode == 0, out.stderr
    current = bridge.store().current("claude/t")
    assert [r.id for r in current] == [old.id]


def test_a_cli_failure_is_a_message_not_a_stack_trace(env):
    out = cli(["--scope", "claude/t", "list"],
              {"CLAUDE_PLUGIN_OPTION_DATABASE": "dbidnope", "HOTDATA_API_KEY": "none"})
    assert "Traceback" not in out.stderr
    assert out.returncode in (0, 1)
