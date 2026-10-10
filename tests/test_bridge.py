"""What the plugin must do: read a note, map it, and carry it through the hooks."""

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

NOTE = """---
name: prefers-short-answers
description: Eddie wants terse, technically accurate explanations
metadata:
  type: feedback
---

Keep explanations short. **Why:** long answers bury the decision.
**How to apply:** lead with the answer, then the reason. See [[other-note]].
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the plugin at a throwaway data directory and memory root."""
    projects = tmp_path / "projects"
    (projects / "a-project" / "memory").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_STORE", "local")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MEMORY_ROOT", str(projects))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SCOPE_PREFIX", "claude")
    return projects


# ----------------------------------------------------------------------- the mapping


def test_frontmatter_gives_the_name_description_type_and_body():
    note = bridge.parse_note(NOTE)
    assert note["name"] == "prefers-short-answers"
    assert note["description"] == "Eddie wants terse, technically accurate explanations"
    assert note["type"] == "feedback"
    assert note["body"].startswith("Keep explanations short.")
    assert "---" not in note["body"]


def test_a_note_without_frontmatter_is_all_body():
    note = bridge.parse_note("just a line\n")
    assert note == {"name": "", "description": "", "type": "project", "body": "just a line"}


def test_an_unknown_type_falls_back_to_project():
    assert bridge.parse_note("---\nmetadata:\n  type: nonsense\n---\nx\n")["type"] == "project"


@pytest.mark.parametrize(
    ("claude_type", "kind"),
    [("user", "profile"), ("feedback", "procedure"), ("project", "fact"), ("reference", "fact")],
)
def test_each_claude_type_maps_to_one_hotmemory_kind(claude_type, kind):
    text = f"---\nname: n\nmetadata:\n  type: {claude_type}\n---\nbody\n"
    fact = bridge.note_to_fact(Path("n.md"), text)
    assert fact.kind == kind
    assert fact.tags == (f"type:{claude_type}",)


def test_the_fact_keeps_the_note_name_description_and_file():
    fact = bridge.note_to_fact(Path("prefers-short-answers.md"), NOTE, session="s1")
    assert fact.subject == "prefers-short-answers"
    assert fact.content.startswith("Eddie wants terse")
    assert "Keep explanations short." in fact.content
    assert fact.sources == ("file:prefers-short-answers.md", "session:s1")
    assert fact.payload == {"note": "prefers-short-answers.md", "claude_type": "feedback"}


def test_an_empty_note_gives_no_fact():
    assert bridge.note_to_fact(Path("n.md"), "---\nname: n\n---\n\n") is None


# ------------------------------------------------------------------ which file counts


def test_a_note_under_the_memory_root_counts(env):
    assert bridge.is_memory_file(env / "a-project" / "memory" / "note.md")


def test_the_index_and_other_files_do_not_count(env):
    memory = env / "a-project" / "memory"
    assert not bridge.is_memory_file(memory / "MEMORY.md")
    assert not bridge.is_memory_file(memory / "note.txt")
    assert not bridge.is_memory_file(Path("/tmp/elsewhere/memory/note.md"))


def test_a_scope_label_drops_a_dot_and_a_slash(env):
    assert bridge.scope_for("/Users/me/Code/my.project") == ("claude", "my-project")


# ------------------------------------------------------------------------- the hooks


def hook(name: str, payload: dict) -> dict:
    """Run one hook script the way Claude Code does, and return what it printed."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SCRIPTS)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else {}


def test_a_written_note_comes_back_at_the_next_session_start(env):
    path = env / "a-project" / "memory" / "prefers-short-answers.md"
    path.write_text(NOTE, encoding="utf-8")
    cwd = "/Users/me/Code/a-project"

    assert hook("capture", {"tool_input": {"file_path": str(path)}, "cwd": cwd, "session_id": "s1"}) == {}

    out = hook("session_start", {"cwd": cwd, "source": "startup"})
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "Eddie wants terse" in context
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_writing_the_same_note_again_adds_nothing(env):
    path = env / "a-project" / "memory" / "n.md"
    path.write_text(NOTE, encoding="utf-8")
    cwd = "/Users/me/Code/a-project"
    for _ in range(3):
        hook("capture", {"tool_input": {"file_path": str(path)}, "cwd": cwd})
    with bridge.memory() as memory:
        records = memory.store.list(("claude", "a-project"), limit=100)
    assert len(records) == 1


def test_a_file_that_is_not_a_note_is_ignored(env):
    other = env.parent / "notes.md"
    other.write_text(NOTE, encoding="utf-8")
    hook("capture", {"tool_input": {"file_path": str(other)}, "cwd": "/x/a-project"})
    with bridge.memory() as memory:
        assert memory.store.list(("claude",), limit=100) == []


def test_another_project_does_not_see_this_project(env):
    path = env / "a-project" / "memory" / "n.md"
    path.write_text(NOTE, encoding="utf-8")
    hook("capture", {"tool_input": {"file_path": str(path)}, "cwd": "/x/a-project"})
    assert hook("session_start", {"cwd": "/x/b-project"}) == {}


def test_a_session_start_with_no_memory_prints_nothing(env):
    assert hook("session_start", {"cwd": "/x/a-project"}) == {}


# ------------------------------------------------------------------------ the cache


def test_a_capture_writes_the_block_that_the_next_session_reads(env):
    path = env / "a-project" / "memory" / "n.md"
    path.write_text(NOTE, encoding="utf-8")
    hook("capture", {"tool_input": {"file_path": str(path)}, "cwd": "/x/a-project"})
    cached = bridge.cache_path(("claude", "a-project")).read_text(encoding="utf-8")
    assert "Eddie wants terse" in cached


def test_session_start_prints_the_cache_without_reading_the_store(env, monkeypatch):
    scope = ("claude", "a-project")
    bridge.write_cache(scope, "cached memory block")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_STORE", "hotdata")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_URL", "http://127.0.0.1:1")
    out = hook("session_start", {"cwd": "/x/a-project"})
    assert out["hookSpecificOutput"]["additionalContext"] == "cached memory block"


def test_an_empty_cache_prints_nothing(env):
    bridge.write_cache(("claude", "a-project"), "")
    assert hook("session_start", {"cwd": "/x/a-project"}) == {}


def test_a_store_that_cannot_be_reached_does_not_stop_a_session(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_STORE", "hotdata")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", "none")
    out = hook("session_start", {"cwd": "/x/never-seen"})
    assert "hotdata-memory:" in out["systemMessage"]
    assert "hookSpecificOutput" not in out


def test_a_store_that_cannot_be_reached_does_not_block_an_edit(env, monkeypatch):
    path = env / "a-project" / "memory" / "n.md"
    path.write_text(NOTE, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_STORE", "hotdata")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_API_KEY", "none")
    out = hook("capture", {"tool_input": {"file_path": str(path)}, "cwd": "/x/a-project"})
    assert "hotdata-memory:" in out["systemMessage"]


# -------------------------------------------------------------------- standalone mode


def test_standalone_tells_claude_how_to_write_when_memory_is_empty(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MODE", "standalone")
    out = hook("session_start", {"cwd": "/x/a-project"})
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "memory remember" in context
    assert "No memory is recorded" in context


def test_standalone_keeps_the_instructions_beside_the_records(env, monkeypatch):
    path = env / "a-project" / "memory" / "n.md"
    path.write_text(NOTE, encoding="utf-8")
    hook("capture", {"tool_input": {"file_path": str(path)}, "cwd": "/x/a-project"})
    bridge.cache_path(("claude", "a-project")).unlink()
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MODE", "standalone")
    context = hook("session_start", {"cwd": "/x/a-project"})["hookSpecificOutput"]["additionalContext"]
    assert "Eddie wants terse" in context
    assert "memory remember" in context


def test_mirror_mode_says_nothing_when_memory_is_empty(env):
    assert hook("session_start", {"cwd": "/x/a-project"}) == {}


# ---------------------------------------------------------- scope does not follow cwd


def test_a_repository_names_the_project_from_any_subdirectory(env, tmp_path):
    repo = tmp_path / "my-repo"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "services" / "api"
    deep.mkdir(parents=True)
    assert bridge.scope_for(str(repo)) == ("claude", "my-repo")
    assert bridge.scope_for(str(deep)) == ("claude", "my-repo")


def test_the_project_option_wins_over_the_directory(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_PROJECT", "pinned")
    assert bridge.scope_for("/anywhere/at/all") == ("claude", "pinned")


def test_outside_a_repository_the_directory_still_names_it(env, tmp_path):
    plain = tmp_path / "loose-dir"
    plain.mkdir()
    assert bridge.scope_for(str(plain)) == ("claude", "loose-dir")


def test_standalone_warns_against_recording_a_computed_figure(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MODE", "standalone")
    context = hook("session_start", {"cwd": "/x/a-project"})["hookSpecificOutput"]["additionalContext"]
    assert "Record how to get an answer, not the answer" in context
    assert "canonical" in context
    assert "the data is right" in context


# ------------------------------------------------- memory keeps methods, not results


@pytest.mark.parametrize(
    ("content", "conclusion"),
    [
        ("The clean set has 2869585 rows; mean fare 17.49; share 0.8009", True),
        ("Fare per mile is 17.49 and the median duration is 8.37 minutes", False),
        ("date_diff does not exist; use EXTRACT(EPOCH FROM (a - b)) for seconds", False),
        ("taxi.public.trips has 19 columns and uppercase names need quoting", False),
        ("Counts: 38341 non-positive fares, 41391 tips over fare, 60371 zero distance", True),
    ],
)
def test_a_record_carrying_several_figures_reads_as_a_conclusion(content, conclusion):
    assert bridge.reads_as_a_conclusion(content) is conclusion


def test_a_version_number_is_not_a_result():
    assert bridge.figures("DataFusion 55 has no date_diff") == []


def test_the_memory_sql_names_this_project_only():
    sql = bridge.memory_sql(("claude", "taxi"))
    assert "mem.public.memory_v1" in sql
    assert "namespace = 'claude/taxi'" in sql


def memcli(args, env_extra=None):
    """Run the memory command the way the skill tells Claude to."""
    import subprocess
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "memcli.py"), *args],
        capture_output=True, text=True,
        env={**os.environ, **(env_extra or {}), "PYTHONPATH": str(SCRIPTS)},
    )


def test_figures_without_a_query_are_refused(env):
    out = memcli(["--scope", "claude/t", "remember",
                  "clean set 2869585 rows, mean fare 17.49, share 0.8009",
                  "--subject", "clean-set"])
    assert out.returncode == 2
    assert "no query to reproduce them" in out.stderr


def test_figures_with_a_query_are_kept(env):
    out = memcli(["--scope", "claude/t", "remember",
                  "clean set 2869585 rows, mean fare 17.49, share 0.8009",
                  "--subject", "clean-set", "--query", "SELECT count(*) FROM trips"])
    assert out.returncode == 0, out.stderr
    with bridge.memory() as memory:
        record = memory.store.list(("claude", "t"), limit=5)[0]
    assert record.payload["query"] == "SELECT count(*) FROM trips"


def test_a_near_repeat_that_disagrees_is_refused(env):
    first = ["--scope", "claude/t", "remember",
             "Clean set keeps rows where fare_amount > 0 and trip_distance > 0",
             "--subject", "clean-set"]
    assert memcli(first).returncode == 0
    second = ["--scope", "claude/t", "remember",
              "Clean set keeps rows where fare_amount > 0 and trip_distance > 0 "
              "and tip_amount <= fare_amount",
              "--subject", "clean-set"]
    out = memcli(second)
    assert out.returncode == 2
    assert "nearly repeats a record already stored" in out.stderr
    assert "memory supersede" in out.stderr
    out = memcli([*second, "--force"])
    assert out.returncode == 0, out.stderr


def test_writing_the_same_fact_again_is_still_free(env):
    args = ["--scope", "claude/t", "remember", "date_diff does not exist here",
            "--subject", "dialect"]
    assert memcli(args).returncode == 0
    assert memcli(args).returncode == 0
    with bridge.memory() as memory:
        assert len(memory.store.list(("claude", "t"), limit=10)) == 1
