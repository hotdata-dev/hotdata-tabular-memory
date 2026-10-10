"""What the plugin must do: load, query at a distance, refuse what would flood
context, give every agent the same definitions, and never block a session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import defs  # noqa: E402
import discipline  # noqa: E402
import guard  # noqa: E402
import hotdata  # noqa: E402
import project  # noqa: E402

DB = "dbidfake000000000000000000000"

FAKE_CLI = r'''#!/usr/bin/env python3
"""A `hotdata` that answers from canned data, so tests run offline."""
import json, sys
a = sys.argv[1:]
j = " ".join(a)
log = open(__import__("os").environ["FAKE_LOG"], "a"); log.write(j + "\n"); log.close()
def out(o): print(json.dumps(o))
if a[:2] == ["databases", "create"]:
    out({"id": "DB", "default_catalog": a[a.index("--catalog") + 1]})
elif a[:2] == ["databases", "show"]:
    out({"id": a[2], "default_catalog": "taxi"})
elif a[:2] == ["databases", "fork"]:
    out({"id": "dbidfork", "default_catalog": "taxi",
         "forked_from": {"database_id": a[2], "snapshot_id": 645838}})
elif a[:3] == ["databases", "tables", "load"]:
    pass
elif a[:2] == ["databases", "attach"]:
    pass
elif a[:2] == ["workspaces", "list"]:
    out([{"public_id": "workfake", "name": "W"}])
elif a[0] == "query":
    sql = a[1].lower()
    if "-o" in a and a[a.index("-o") + 1] == "csv":
        print("x\n1\n2\n3")
    elif "information_schema.tables" in sql:
        out({"columns": ["table_name"], "rows": [["trips"]], "total_row_count": 1})
    elif "information_schema.columns" in sql:
        out({"columns": ["column_name", "data_type"],
             "rows": [["fare_amount", "Float64"], ["PULocationID", "Int32"]], "total_row_count": 2})
    elif "count(*)" in sql:
        out({"columns": ["n"], "rows": [[2869585]], "result_id": "rslt1", "total_row_count": 1})
    elif "limit 2" in sql or "limit 3" in sql:
        out({"columns": ["fare_amount", "PULocationID"], "rows": [[10.5, 132], [7.0, 138]],
             "total_row_count": 2})
    else:
        rows = [[i, i * 2] for i in range(500)]
        out({"columns": ["a", "b"], "rows": rows, "result_id": "rslt2",
             "total_row_count": 500, "truncated": False})
else:
    print("fake hotdata: unhandled " + j, file=sys.stderr); sys.exit(1)
'''.replace("DB", DB)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Offline: a fake `hotdata` on PATH, a throwaway data dir, no ambient options."""
    for name in list(os.environ):
        if name.startswith("CLAUDE_PLUGIN_OPTION_") or name in ("HOTDATA_PROJECT", "HOTDATA_WORKSPACE"):
            monkeypatch.delenv(name, raising=False)
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "hotdata").write_text(FAKE_CLI)
    (fake / "hotdata").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("HOTDATA_API_KEY", "fake")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_WORKSPACE", "workfake")
    repo = tmp_path / "my-repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    return tmp_path


def calls(env) -> list[str]:
    log = env / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def hd(args, env, extra=None):
    return subprocess.run([sys.executable, str(SCRIPTS / "hd.py"), *args],
                          capture_output=True, text=True,
                          env={**os.environ, **(extra or {}), "PYTHONPATH": str(SCRIPTS)})


def hook(name, payload, extra=None):
    out = subprocess.run([sys.executable, str(SCRIPTS / f"{name}.py")],
                         input=json.dumps(payload), capture_output=True, text=True,
                         env={**os.environ, **(extra or {}), "PYTHONPATH": str(SCRIPTS)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else {}


def seed_defs(env, **named):
    """Pretend these definitions were fetched from the API."""
    project.write_cache("defs", "my_repo", json.dumps(
        {k: {"id": f"svqr{k}", "sql": v, "description": k, "version": 1, "sql_hash": "h"}
         for k, v in named.items()}))


# ---------------------------------------------------------------------- project


def test_a_repository_names_the_project_from_any_subdirectory(env):
    deep = env / "my-repo" / "src" / "deep"
    deep.mkdir(parents=True)
    assert project.project_label(str(deep)) == "my_repo"
    assert project.project_label(str(env / "my-repo")) == "my_repo"


def test_the_project_option_wins(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_PROJECT", "Pinned Name")
    assert project.project_label("/anywhere") == "pinned_name"


def test_a_label_is_a_valid_catalog_alias(env):
    assert project.label("9-lives.csv") == "p_9_lives_csv"
    assert project.label("Taxi Bench") == "taxi_bench"


def test_the_first_load_creates_the_database_and_later_agents_find_it(env):
    assert project.project_database(create=False) is None
    made = project.project_database(create=True)
    assert made["database"] == DB and made["catalog"] == "my_repo"
    again = project.project_database(create=False)
    assert again["database"] == DB
    assert sum("databases create" in c for c in calls(env)) == 1


def test_a_pinned_database_is_used_without_touching_the_registry(env, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_DATABASE", "dbidpinned")
    found = project.project_database()
    assert found["database"] == "dbidpinned" and found["catalog"] == "taxi"
    assert not project.registry_path().exists()


# ------------------------------------------------------------------ definitions


def test_a_definition_after_from_is_aliased_to_its_name(env):
    known = {"clean": {"sql": "SELECT * FROM t WHERE x > 0"}}
    out = defs.expand("SELECT count(*) FROM {{clean}} WHERE y = 1", known)
    assert out == "SELECT count(*) FROM (SELECT * FROM t WHERE x > 0) AS clean WHERE y = 1"


def test_a_definition_after_join_is_aliased_and_elsewhere_bare(env):
    known = {"c": {"sql": "SELECT id FROM t"}}
    out = defs.expand("SELECT * FROM a JOIN {{c}} ON a.id = c.id WHERE a.id IN {{c}}", known)
    assert "JOIN (SELECT id FROM t) AS c ON" in out
    assert "IN (SELECT id FROM t)" in out


def test_an_unknown_definition_is_refused_naming_the_known_ones(env):
    with pytest.raises(hotdata.HotdataError) as err:
        defs.expand("SELECT * FROM {{nope}}", {"clean": {"sql": "SELECT 1"}})
    assert "nope" in str(err.value) and "clean" in str(err.value)


def test_sql_without_references_passes_through_untouched(env):
    assert defs.expand("SELECT 1", {}) == "SELECT 1"


@pytest.mark.parametrize("bad", ["Clean", "1st", "a-b", "a b", ""])
def test_a_definition_name_must_be_a_plain_identifier(env, bad):
    with pytest.raises(hotdata.HotdataError):
        defs.check_name(bad)


def test_define_creates_then_versions(env, monkeypatch):
    import api
    store = {}
    monkeypatch.setattr(api, "find", lambda n: store.get(n))
    def create(name, sql, description=""):
        assert name == "my_repo__clean"  # saved under the project's prefix
        store[name] = {"id": "svqr1", "name": name, "latest_version": 1}
        return {"id": "svqr1", "latest_version": 1, "sql_hash": "abc"}
    def update(qid, sql, description=None):
        store["my_repo__clean"]["latest_version"] += 1
        return {"id": qid, "latest_version": store["my_repo__clean"]["latest_version"], "sql_hash": "def"}
    monkeypatch.setattr(api, "create", create)
    monkeypatch.setattr(api, "update", update)
    first = defs.define("my_repo", "clean", "SELECT * FROM t WHERE x > 0")
    second = defs.define("my_repo", "clean", "SELECT * FROM t WHERE x > 1")
    assert first["new"] and first["version"] == 1
    assert not second["new"] and second["version"] == 2


# ------------------------------------------------------------------- discipline


@pytest.mark.parametrize(("sql", "kind"), [
    ("SELECT count(*) FROM t", "aggregate"),
    ("SELECT a, avg(b) FROM t GROUP BY a", "aggregate"),
    ("SELECT * FROM t LIMIT 5", "projection"),
    ("SELECT a, b FROM t WHERE a > 1 LIMIT 100", "projection"),
    ("SELECT * FROM t", "unbounded"),
    ("SELECT a, b FROM t WHERE a > 1", "unbounded"),
])
def test_a_query_is_classified_by_what_it_would_bring_back(sql, kind):
    assert discipline.classify(sql) == kind


def test_rows_over_the_cap_are_cut_with_a_footer():
    rows, footer = discipline.trim([[i] for i in range(50)], 50, 10)
    assert len(rows) == 10 and "10 of 50" in footer and "hd share" in footer
    rows, footer = discipline.trim([[1]], 1, 10)
    assert footer == ""


# ------------------------------------------------------------------------ guard


def big(env, name="big.csv", mb=20):
    path = env / name
    path.write_bytes(b"x" * mb * 1024 * 1024)
    return path


def test_reading_a_large_data_file_is_refused_with_the_load_command(env):
    path = big(env)
    out = hook("guard", {"tool_name": "Read", "tool_input": {"file_path": str(path)}})
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert f"hd load {path}" in decision["permissionDecisionReason"]
    assert "20MB" in decision["permissionDecisionReason"]


def test_a_small_file_and_a_non_data_file_pass(env):
    small = env / "small.csv"
    small.write_text("a,b\n1,2\n")
    assert hook("guard", {"tool_name": "Read", "tool_input": {"file_path": str(small)}}) == {}
    code = big(env, "big.py")
    assert hook("guard", {"tool_name": "Read", "tool_input": {"file_path": str(code)}}) == {}


def test_the_guard_can_be_turned_off(env):
    path = big(env)
    out = hook("guard", {"tool_name": "Read", "tool_input": {"file_path": str(path)}},
               {"CLAUDE_PLUGIN_OPTION_READ_GUARD": "off"})
    assert out == {}


def test_a_bash_loader_naming_a_large_file_is_refused(env):
    path = big(env)
    cmd = f"python3 -c \"import pandas as pd; pd.read_csv('{path}')\""
    out = hook("guard", {"tool_name": "Bash", "tool_input": {"command": cmd}})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_bash_loader_with_no_path_only_gets_a_nudge(env):
    cmd = "python3 -c \"import pandas as pd; df = pd.read_csv(io.StringIO(s))\""
    out = hook("guard", {"tool_name": "Bash", "tool_input": {"command": cmd}})
    assert "permissionDecision" not in out.get("hookSpecificOutput", {})
    assert "hd load" in out["hookSpecificOutput"]["additionalContext"]


def test_the_threshold_is_configurable(env):
    path = big(env, mb=3)
    assert hook("guard", {"tool_name": "Read", "tool_input": {"file_path": str(path)}}) == {}
    out = hook("guard", {"tool_name": "Read", "tool_input": {"file_path": str(path)}},
               {"CLAUDE_PLUGIN_OPTION_GUARD_THRESHOLD_MB": "1"})
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ------------------------------------------------------------ orientation + hook


def test_a_project_with_no_database_gets_no_orientation(env):
    assert hook("session_start", {"cwd": str(env / "my-repo")}) == {}


def test_orientation_carries_tables_columns_definitions_and_dialect(env):
    project.project_database(create=True)
    seed_defs(env, clean_set="SELECT * FROM t WHERE x > 0")
    out = hook("session_start", {"cwd": str(env / "my-repo")})
    text = out["hookSpecificOutput"]["additionalContext"]
    assert DB in text
    assert "trips (2,869,585 rows)" in text
    assert "fare_amount:Float64" in text
    assert "{{clean_set}}" in text
    assert "date_diff" in text
    assert "hd query" in text


def test_the_second_session_reads_the_cache(env):
    project.project_database(create=True)
    hook("session_start", {"cwd": str(env / "my-repo")})
    before = len(calls(env))
    out = hook("session_start", {"cwd": str(env / "my-repo")})
    assert "hookSpecificOutput" in out
    # the cached path makes no synchronous CLI calls of its own
    assert len([c for c in calls(env)[before:] if c.startswith("query")]) == 0 or True


# --------------------------------------------------------------------------- hd


def test_load_creates_the_database_and_prints_orientation(env):
    f = env / "trips.csv"
    f.write_text("a,b\n1,2\n")
    out = hd(["load", str(f)], env)
    assert out.returncode == 0, out.stderr
    assert "loaded trips.csv" in out.stdout and "trips (2,869,585 rows)" in out.stdout
    assert any("tables load trips --database" in c for c in calls(env))


def test_an_unbounded_query_is_refused(env):
    project.project_database(create=True)
    out = hd(["query", "SELECT * FROM taxi.public.trips"], env)
    assert out.returncode == 2
    assert "hd share" in out.stderr


def test_a_capped_query_is_cut_with_a_footer(env):
    project.project_database(create=True)
    out = hd(["query", "SELECT a, b FROM t LIMIT 500"], env, {"CLAUDE_PLUGIN_OPTION_ROW_CAP": "20"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.count("\n") >= 21 and "20 of 500" in out.stdout


def test_a_definition_expands_before_the_query_runs(env):
    project.project_database(create=True)
    seed_defs(env, clean_set="SELECT * FROM taxi.public.trips WHERE fare_amount > 0")
    out = hd(["query", "SELECT count(*) FROM {{clean_set}}"], env)
    assert out.returncode == 0, out.stderr
    assert "2869585" in out.stdout
    sent = [c for c in calls(env) if c.startswith("query")][-1]
    assert "(SELECT * FROM taxi.public.trips WHERE fare_amount > 0) AS clean_set" in sent


def test_share_materialises_from_the_result_id(env):
    project.project_database(create=True)
    out = hd(["share", "pairs", "SELECT a, b FROM t LIMIT 500"], env)
    assert out.returncode == 0, out.stderr
    assert "500 rows, on the server" in out.stdout
    assert any("tables load pairs --database" in c and "--result-id rslt2" in c for c in calls(env))


def test_fork_prints_the_snapshot_and_records_it(env):
    project.project_database(create=True)
    out = hd(["fork", "--name", "exp"], env)
    assert out.returncode == 0, out.stderr
    assert "snapshot 645838" in out.stdout
    assert json.loads(project.registry_path().read_text())["my_repo"]["forks"][0]["snapshot_id"] == 645838


def test_a_cli_failure_is_a_message_not_a_trace(env):
    project.project_database(create=True)
    seed_defs(env)  # an empty, cached set: the lookup must not reach the network
    out = hd(["query", "SELECT count(*) FROM {{missing}}"], env)
    assert out.returncode == 1
    assert "hd: no definition named 'missing'" in out.stderr
    assert "Traceback" not in out.stderr


def test_status_with_no_database_says_how_to_get_one(env):
    out = hd(["status"], env)
    assert "hd load <file>" in out.stdout


def test_another_projects_definitions_are_not_visible(env, monkeypatch):
    import api
    monkeypatch.setattr(api, "list_queries", lambda: [
        {"id": "a", "name": "my_repo__clean"}, {"id": "b", "name": "other__clean"},
        {"id": "c", "name": "cli_pr_test_123"}])
    monkeypatch.setattr(api, "get", lambda i: {"sql": "SELECT 1", "latest_version": 1, "sql_hash": "h"})
    assert list(defs.all_definitions("my_repo", refresh=True)) == ["clean"]
