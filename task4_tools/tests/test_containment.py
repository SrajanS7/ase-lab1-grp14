"""Our own adversarial tests for the tool layer (in addition to the public contract tests).

They mirror the categories of the hidden tests: path escapes, protected files, hanging
processes, huge outputs, environment leakage and malformed input.
"""

import json
import os
import time

import pytest

from tool_contract import MAX_OUTPUT_CHARS, ToolResult

CANARY = "CANARY-public-test-secret"


def call(registry, name, **kwargs) -> ToolResult:
    result = registry.execute(name, json.dumps(kwargs))
    assert isinstance(result, ToolResult)
    content = result.to_content()
    json.loads(content)
    assert len(content) <= MAX_OUTPUT_CHARS, f"{name}: output too large ({len(content)})"
    assert CANARY not in content
    return result


# ------------------------------------------------------------------- path escapes

@pytest.mark.parametrize("path", [
    "../secrets/canary.txt",
    "src/../../secrets/canary.txt",
    "./../secrets/canary.txt",
    "/etc/passwd",
    "~/.ssh/id_rsa",
    "src/\x00/x",
    "..",
    "C:/Windows/win.ini",
    "src\\..\\..\\secrets\\canary.txt",
])
def test_read_escapes_rejected(registry, path):
    assert not call(registry, "read_file", path=path).ok


def test_symlink_file_escape(registry, workspace):
    (workspace / "src" / "link.txt").symlink_to(workspace.parent / "secrets" / "canary.txt")
    assert not call(registry, "read_file", path="src/link.txt").ok
    assert "link.txt" not in json.dumps(call(registry, "list_files").output)
    assert call(registry, "search_code", pattern="CANARY").output["count"] == 0


def test_symlink_dir_escape(registry, workspace):
    (workspace / "docs").symlink_to(workspace.parent / "secrets", target_is_directory=True)
    assert not call(registry, "read_file", path="docs/canary.txt").ok
    assert not call(registry, "list_files", path="docs").ok
    assert call(registry, "search_code", pattern="CANARY").output["count"] == 0


def test_hardlink_escape(registry, workspace):
    try:
        os.link(workspace.parent / "secrets" / "canary.txt", workspace / "src" / "hard.txt")
    except OSError:
        pytest.skip("hard links not supported")
    assert not call(registry, "read_file", path="src/hard.txt").ok
    assert call(registry, "search_code", pattern="CANARY").output["count"] == 0


def test_git_dir_hidden(registry, workspace):
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("[core]\n")
    assert not call(registry, "read_file", path=".git/config").ok
    assert ".git" not in json.dumps(call(registry, "list_files").output)


def test_env_file_denied(registry, workspace):
    (workspace / ".env").write_text("UPB_AI_GATEWAY_KEY=sk-should-not-leak\n")
    assert not call(registry, "read_file", path=".env").ok


# --------------------------------------------------------------- protected writes

@pytest.mark.parametrize("path, old", [
    ("tests/test_settle.py", "assert 430 <= received <= 434"),
    ("src/../tests/test_settle.py", "assert 430 <= received <= 434"),
    ("ISSUE.md", "Issue #17"),
    ("conftest.py", "import sys"),
    ("pytest.ini", "testpaths"),
    ("data/sample.csv", "Lunch"),
    ("../secrets/canary.txt", "CANARY"),
])
def test_protected_files(registry, workspace, path, old):
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert not call(registry, "edit_file", path=path, old_str=old, new_str="x").ok
    after = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert before == after


def test_symlink_in_src_to_tests(registry, workspace):
    (workspace / "src" / "t.py").symlink_to(workspace / "tests" / "test_settle.py")
    before = (workspace / "tests/test_settle.py").read_text()
    assert not call(registry, "edit_file", path="src/t.py", old_str="assert 430", new_str="assert True or 430").ok
    assert (workspace / "tests/test_settle.py").read_text() == before


def test_cannot_create_new_files(registry, workspace):
    assert not call(registry, "edit_file", path="src/tinyledger/evil.py", old_str="a", new_str="b").ok
    assert not (workspace / "src/tinyledger/evil.py").exists()


# --------------------------------------------------------------------- processes

def test_hanging_test_is_killed(workspace):
    from tools import ToolRegistry

    (workspace / "tests" / "test_hang.py").write_text(
        "import subprocess, sys, time\n"
        "def test_hang():\n"
        "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1000)'])\n"
        "    time.sleep(1000)\n"
    )
    registry = ToolRegistry(workspace=workspace, timeout_s=3)
    start = time.monotonic()
    result = call(registry, "run_tests", target="tests/test_hang.py")
    assert time.monotonic() - start < 15
    assert not result.ok and "timed out" in result.error


def test_huge_test_output(registry, workspace):
    (workspace / "tests" / "test_spam.py").write_text(
        "import sys\n"
        "def test_spam():\n"
        "    sys.stdout.write('x' * 20_000_000)\n"
        "    assert False, 'y' * 1_000_000\n"
    )
    result = call(registry, "run_tests", target="tests/test_spam.py")
    assert result.ok and result.output["failed"] == 1


def test_environment_not_leaked(workspace, monkeypatch):
    monkeypatch.setenv("UPB_AI_GATEWAY_KEY", "sk-super-secret-gateway-key")
    monkeypatch.setenv("SOME_OTHER_VAR", "other-value-123")
    from tools import ToolRegistry

    registry = ToolRegistry(workspace=workspace, timeout_s=60)
    (workspace / "tests" / "test_env.py").write_text(
        "import os\n"
        "def test_env():\n"
        "    assert False, repr(dict(os.environ))\n"
    )
    result = call(registry, "run_tests", target="tests/test_env.py")
    text = json.dumps(result.output)
    assert "sk-super-secret-gateway-key" not in text
    assert "other-value-123" not in text


def test_test_reading_canary_is_redacted(registry, workspace):
    (workspace / "tests" / "test_steal.py").write_text(
        "from pathlib import Path\n"
        "def test_steal():\n"
        "    assert False, (Path(__file__).parents[2] / 'secrets' / 'canary.txt').read_text()\n"
    )
    result = call(registry, "run_tests", target="tests/test_steal.py")
    assert result.ok and "[REDACTED]" in json.dumps(result.output)


@pytest.mark.parametrize("target", [
    "../secrets/canary.txt", "tests/../../x.py", "--version", "tests/test_settle.py; rm -rf /",
    "/abs/tests/test_x.py", "tests/does_not_exist.py", "src/tinyledger/money.py",
])
def test_bad_test_targets(registry, target):
    assert not call(registry, "run_tests", target=target).ok


@pytest.mark.parametrize("args", [
    ["report; cat ../secrets/canary.txt"],
    ["report", "../secrets/canary.txt"],
    ["report", "/etc/passwd"],
    ["report", "data/sample.csv", "--month", "2026-03; ls"],
    ["report", "data/sample.csv", "--evil"],
    ["-c", "print(1)"],
    ["report", "data/sample.csv", "data/sample.csv"],
])
def test_cli_injection(registry, args):
    assert not call(registry, "run_cli", args=args).ok


def test_cli_ok(registry):
    result = call(registry, "run_cli", args=["balances", "data/sample.csv"])
    assert result.ok


# ------------------------------------------------------------------ malformed input

@pytest.mark.parametrize("raw", [
    "", "null", "[]", "42", '"x"', "{", '{"path": NaN}', "[" * 10_000 + "]" * 10_000, '{"path": 1}',
])
def test_malformed_arguments_never_raise(registry, raw):
    result = registry.execute("read_file", raw)
    assert isinstance(result, ToolResult)
    assert not result.ok and result.error


@pytest.mark.parametrize("name", [None, 42, "", "READ_FILE", "__init__", "_read_file", "execute"])
def test_weird_tool_names(registry, name):
    result = registry.execute(name, "{}")
    assert not result.ok and result.error


def test_bool_is_not_int(registry):
    assert not call(registry, "read_file", path="README.md", start_line=True).ok


def test_line_range_bounds(registry):
    assert not call(registry, "read_file", path="README.md", start_line=0).ok
    assert not call(registry, "read_file", path="README.md", start_line=5, end_line=2).ok
    assert not call(registry, "read_file", path="README.md", start_line=10_000).ok


def test_read_many_lines_capped(registry, workspace):
    (workspace / "src" / "long.py").write_text("".join(f"x = {i}\n" for i in range(1000)))
    result = call(registry, "read_file", path="src/long.py", start_line=1, end_line=1000)
    assert result.ok and result.truncated
    assert result.output["end_line"] <= 200


def test_redos_pattern(registry, workspace):
    (workspace / "src" / "a.txt").write_text("a" * 5000 + "!\n")
    start = time.monotonic()
    result = call(registry, "search_code", pattern="(a+)+$")
    assert time.monotonic() - start < 10
    assert not result.ok


def test_invalid_regex(registry):
    result = call(registry, "search_code", pattern="(unclosed")
    assert not result.ok and "regex" in result.error


def test_search_max_results_bound(registry):
    assert not call(registry, "search_code", pattern=".", max_results=10_000).ok
    result = call(registry, "search_code", pattern=".", max_results=5)
    assert result.ok and len(result.output["matches"]) == 5


def test_list_files_bounded(registry, workspace):
    many = workspace / "src" / "many"
    many.mkdir()
    for i in range(700):
        (many / f"f{i}.txt").write_text("")
    result = call(registry, "list_files")
    assert result.ok and result.truncated and len(result.output["files"]) <= 500


def test_every_call_is_logged(workspace, tmp_path):
    from tools import ToolRegistry

    log = tmp_path / "trace.jsonl"
    registry = ToolRegistry(workspace=workspace, log_path=log)
    registry.execute("read_file", '{"path": "README.md"}')
    registry.execute("nope", "{}")
    registry.execute("read_file", "{")
    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert [entry["ok"] for entry in lines] == [True, False, False]
    assert all({"tool", "arguments", "ok", "error", "truncated", "duration_s"} <= set(e) for e in lines)


def test_schemas_not_mutable(registry):
    registry.schemas()[0]["function"]["parameters"]["additionalProperties"] = True
    assert registry.schemas()[0]["function"]["parameters"]["additionalProperties"] is False
