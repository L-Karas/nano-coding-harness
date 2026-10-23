"""grep 工具：两条执行分支（ripgrep / Python 回退）的单行记录、路径边界与结果一致性。

回归点：rg 是语义基准——隐藏 / .gitignore / 二进制 / 符号链接的取舍两条分支必须一致；
`--glob` 不得绕过 ignore / hidden 规则、`-` 开头的 pattern 不得被当成命令行开关、
输出记录必须单行、截断不得 off-by-one、`path` 缺省为工作目录且不得逃出工作目录。
"""
import asyncio
import locale
import re
import shutil
import subprocess

import pytest

import core.tools.base_tools.grep as grep_mod
from core.runtime_context import ToolContext

_RECORD_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<content>.*)$")


def _engines() -> list[str]:
    return ["python", "ripgrep"] if shutil.which("rg") else ["python"]


def _build_mixed(root) -> None:
    """一颗混合树：可见 / 隐藏 / gitignore / 二进制 / 非 UTF-8 / BOM / CRLF / 子目录。"""
    (root / "sub").mkdir(parents=True)
    (root / "visible.py").write_text("needle\n", encoding="utf-8")
    (root / "sub" / "deep.py").write_text("needle deep\n", encoding="utf-8")
    (root / ".hidden.py").write_text("needle\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (root / "ignored.py").write_text("needle\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / "binary.dat").write_bytes(b"needle\x00tail\n")
    (root / "latin.txt").write_bytes(b"needle caf\xe9\n")
    (root / "bom.py").write_bytes(b"\xef\xbb\xbfneedle\n")
    (root / "crlf.txt").write_bytes(b"needle\r\n")


@pytest.mark.parametrize("pattern", ["needle", "^needle", "deep$", ""])
def test_engines_return_identical_output(monkeypatch, tmp_path, pattern):
    """同一棵树上两条分支必须给出完全相同的输出（rg 是语义基准）。"""
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    _build_mixed(tmp_path)

    outputs = {engine: _grep(monkeypatch, tmp_path, engine=engine, pattern=pattern) for engine in _engines()}

    assert len(set(outputs.values())) == 1, outputs


def test_lookbehind_pattern_works_on_both_engines(monkeypatch, tmp_path):
    """`pattern` 以 Python `re` 为准：rg 不支持 lookaround，不能因此静默丢结果。"""
    (tmp_path / "a.txt").write_text("needle 42\n", encoding="utf-8")

    for engine in _engines():
        assert _grep(monkeypatch, tmp_path, engine=engine, pattern=r"(?<=needle )\d+") == \
            f"{tmp_path / 'a.txt'}:1:needle 42"


def test_dollar_does_not_match_crlf_line_end(monkeypatch, tmp_path):
    """rg 的行模型：`\\r` 属于行内容，所以 `needle$` 不命中 CRLF 行尾。"""
    (tmp_path / "crlf.txt").write_bytes(b"needle\r\n")

    for engine in _engines():
        assert _grep(monkeypatch, tmp_path, engine=engine, pattern="needle$") == "(No matches)"


def test_lone_cr_is_one_line(monkeypatch, tmp_path):
    """只有 `\\r` 的文件是一整行（rg 只按 `\\n` 切行）。"""
    (tmp_path / "cr.txt").write_bytes(b"needle a\rneedle b\r")

    for engine in _engines():
        assert _grep(monkeypatch, tmp_path, engine=engine, pattern="needle") == \
            f"{tmp_path / 'cr.txt'}:1:needle a\rneedle b"


def test_last_line_without_newline_is_reported(monkeypatch, tmp_path):
    (tmp_path / "last.txt").write_bytes(b"first\nneedle last")

    for engine in _engines():
        assert _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="needle")) == [
            ("last.txt", 2, "needle last")]


def test_file_pattern_filters_both_engines(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")

    for engine in _engines():
        assert [name for name, _, _ in _names(
            _grep(monkeypatch, tmp_path, engine=engine, pattern="needle", file_pattern="*.py"))] == ["a.py"]


def test_bom_is_only_stripped_at_file_start(monkeypatch, tmp_path):
    """BOM 只在文件开头被吃掉，行中的 BOM 与 rg 一样保留。"""
    (tmp_path / "bom2.txt").write_bytes(b"x\n\xef\xbb\xbfneedle\n")

    for engine in _engines():
        assert _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="needle")) == [
            ("bom2.txt", 2, "\ufeffneedle")]


def test_hidden_file_whitelisted_by_gitignore_is_skipped(monkeypatch, tmp_path):
    """rg 会搜 .gitignore 白名单里的隐藏文件（`--hidden` 文档明说）；两条分支都必须按 `skip_hidden_and_ignored` 跳过。"""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text(".*\n!.hidden.py\n", encoding="utf-8")
    (tmp_path / ".hidden.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "kept.py").write_text("needle\n", encoding="utf-8")

    for engine in _engines():
        assert [name for name, _, _ in _names(
            _grep(monkeypatch, tmp_path, engine=engine, pattern="needle"))] == ["kept.py"]


@pytest.mark.parametrize("payload", [
    b"needle\x00needle\n",
    b"needle\n" + b"a" * 1000 + b"\x00needle\n",
])
def test_nul_file_is_skipped_by_both_engines(monkeypatch, tmp_path, payload):
    """含 NUL 的文件整体不算命中（rg 打印 "binary file matches"，不出记录）。"""
    (tmp_path / "blob.dat").write_bytes(payload)

    for engine in _engines():
        assert _grep(monkeypatch, tmp_path, engine=engine, pattern="needle") == "(No matches)"


def test_locale_does_not_change_non_utf8_results(monkeypatch, tmp_path):
    """rg 输出固定按 UTF-8 解码：不能让本机单字节代码页把 `caf\\xe9` 解成 é。"""
    (tmp_path / "latin.txt").write_bytes(b"caf\xe9 needle\n")
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *args, **kwargs: "cp1252")

    for engine in _engines():
        assert _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="needle")) == [
            ("latin.txt", 1, "caf\ufffd needle")]


def test_skip_hidden_and_ignored_false_includes_hidden_and_gitignored(monkeypatch, tmp_path):
    """`skip_hidden_and_ignored=false` 放开隐藏 / .gitignore（与 glob 对齐）；默认仍然跳过。"""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (tmp_path / "kept.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".hidden.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("needle\n", encoding="utf-8")

    for engine in _engines():
        assert sorted(name for name, _, _ in _names(
            _grep(monkeypatch, tmp_path, engine=engine, pattern="needle"))) == ["kept.py"]
        assert sorted(name for name, _, _ in _names(
            _grep(monkeypatch, tmp_path, engine=engine, pattern="needle", skip_hidden_and_ignored=False))) == [
            ".hidden.py", "config", "ignored.py", "kept.py"]


@pytest.mark.parametrize("skip_hidden_and_ignored", [True, False])
def test_engines_agree_on_skip_hidden_and_ignored(monkeypatch, tmp_path, skip_hidden_and_ignored):
    """两条分支在两种 `skip_hidden_and_ignored` 下都必须输出完全相同的结果。"""
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    _build_mixed(tmp_path)

    outputs = {engine: _grep(monkeypatch, tmp_path, engine=engine, pattern="needle",
                             skip_hidden_and_ignored=skip_hidden_and_ignored)
               for engine in _engines()}

    assert len(set(outputs.values())) == 1, outputs


def test_python_fallback_skips_hidden_gitignored_and_binary(monkeypatch, tmp_path):
    """回退分支对齐 rg：隐藏 / gitignore / `.git` / 含 NUL 的二进制一律不搜。"""
    _build_mixed(tmp_path)

    assert sorted(name for name, _, _ in _names(
        _grep(monkeypatch, tmp_path, engine="python", pattern="needle"))) == [
        "bom.py", "crlf.txt", "deep.py", "latin.txt", "visible.py"]


def test_utf16_file_is_searched_like_ripgrep(monkeypatch, tmp_path):
    """rg 认 UTF-16 BOM 并转码后搜：回退分支不得把这种文件当二进制跳过。"""
    (tmp_path / "u16.txt").write_bytes("needle utf16\n".encode("utf-16"))

    for engine in _engines():
        assert _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="needle")) == [
            ("u16.txt", 1, "needle utf16")]


def test_partial_failure_keeps_matches_and_warns(monkeypatch, tmp_path):
    """rg 退出码 2（某个子目录不可读）不得让整次搜索失败：保留命中并附警告。"""
    hit = tmp_path / "a.py"
    hit.write_text("needle\n", encoding="utf-8")

    async def fake_run_rg(command, tctx=None):
        return f"{hit}:1:needle\n", "rg: sub: Permission denied\n", 2

    monkeypatch.setattr(grep_mod, "has_ripgrep", lambda: True)
    monkeypatch.setattr(grep_mod, "run_rg", fake_run_rg)

    out = asyncio.run(grep_mod.Grep(pattern="needle", path="").arun(ToolContext(cwd=tmp_path)))

    assert out.splitlines() == [f"{hit}:1:needle", "(Search incomplete: rg: sub: Permission denied)"]


def test_sync_entry_searches_without_an_event_loop(monkeypatch, tmp_path):
    """`run()` 是同步入口（tool_pool 之外的消费者也会用）：它直接走回退分支。"""
    target = tmp_path / "a.py"
    target.write_text("needle\n", encoding="utf-8")
    monkeypatch.setattr(grep_mod, "WORKDIR", tmp_path)

    assert grep_mod.Grep(pattern="needle").run(ToolContext(cwd=tmp_path)) == f"{target}:1:needle"


def test_skip_hidden_and_ignored_is_part_of_the_tool_schema():
    """skip_hidden_and_ignored 由 LLM 决定，必须是工具参数而不是内部开关（与 glob 同名同义）。"""
    schema = grep_mod.Grep.to_openai_tool()["function"]
    assert schema["parameters"]["properties"]["skip_hidden_and_ignored"]["default"] is True
    assert "skip_hidden_and_ignored" in schema["description"]


def _grep(monkeypatch, root, *, engine: str = "python", **kwargs) -> str:
    monkeypatch.setattr(grep_mod, "has_ripgrep", lambda: engine == "ripgrep")
    monkeypatch.setattr(grep_mod, "WORKDIR", root)
    return asyncio.run(grep_mod.Grep(**kwargs).arun(ToolContext(cwd=root)))


def _records(out: str) -> list[tuple[str, int, str]]:
    assert "truncated" not in out
    return [(m["path"], int(m["line"]), m["content"]) for m in map(_RECORD_RE.fullmatch, out.splitlines())]


def _names(out: str) -> list[tuple[str, int, str]]:
    return [(path.replace("\\", "/").rsplit("/", 1)[-1], line, content) for path, line, content in _records(out)]


def _build(root) -> None:
    (root / "src" / "nested").mkdir(parents=True)
    (root / "src" / "a.py").write_text("needle one\nother line\n", encoding="utf-8")
    (root / "src" / "nested" / "deep.py").write_text("deep needle\n", encoding="utf-8")
    (root / "notes.md").write_text("needle md\n", encoding="utf-8")


def test_path_defaults_to_working_directory(monkeypatch, tmp_path):
    """path 省略时搜工作目录（字段缺省值必须真的可用）。"""
    _build(tmp_path)

    for engine in _engines():
        assert _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="needle md")) == [
            ("notes.md", 1, "needle md")]


def test_path_may_be_a_file(monkeypatch, tmp_path):
    """path 指向文件时只搜该文件，不得静默扩大成父目录。"""
    _build(tmp_path)

    for engine in _engines():
        assert _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="needle", path="src/a.py")) == [
            ("a.py", 1, "needle one")]


@pytest.mark.parametrize("escape", ["outside", "dotdot"])
def test_path_cannot_escape_workdir(monkeypatch, tmp_path, escape):
    """工作目录外的绝对路径（含 `..` 形式）必须报错。"""
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.txt").write_text("needle\n", encoding="utf-8")
    path = str(outside) if escape == "outside" else str(work / ".." / "outside")

    for engine in _engines():
        with pytest.raises(Exception, match="escapes work directory"):
            _grep(monkeypatch, work, engine=engine, pattern="needle", path=path)


def test_pattern_starting_with_dash_is_a_pattern(monkeypatch, tmp_path):
    """`-needle` 是正则而不是命令行开关：只命中字面量行，不得静默改成 edle。"""
    (tmp_path / "dash.txt").write_text("-needle\nneedle\n", encoding="utf-8")

    for engine in _engines():
        assert _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="-needle")) == [
            ("dash.txt", 1, "-needle")]


def test_records_are_single_line(monkeypatch, tmp_path):
    """内容含引号 / CRLF 时仍是单行记录，内容原样保留。"""
    (tmp_path / "quotes.py").write_text('needle "quoted" value\n', encoding="utf-8")
    (tmp_path / "crlf.txt").write_bytes(b"needle\r\n")

    for engine in _engines():
        out = _grep(monkeypatch, tmp_path, engine=engine, pattern="needle")
        assert sorted(_names(out)) == [
            ("crlf.txt", 1, "needle"), ("quotes.py", 1, 'needle "quoted" value')]
        assert "\r" not in out


def test_utf8_bom_is_not_part_of_content(monkeypatch, tmp_path):
    (tmp_path / "bom.py").write_bytes(b"\xef\xbb\xbfneedle\n")

    for engine in _engines():
        assert _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="^needle")) == [
            ("bom.py", 1, "needle")]


def test_no_matches_placeholder(monkeypatch, tmp_path):
    _build(tmp_path)

    for engine in _engines():
        assert _grep(monkeypatch, tmp_path, engine=engine, pattern="no-such-needle") == "(No matches)"


@pytest.mark.parametrize("truncated", [False, True])
def test_match_limit_boundary(monkeypatch, tmp_path, truncated):
    """恰好 _MATCH_LIMIT 条不截断；多一条才截断，且截断后仍给满 _MATCH_LIMIT 条。"""
    limit = grep_mod._MATCH_LIMIT
    (tmp_path / "many.txt").write_text("needle\n" * (limit + int(truncated)), encoding="utf-8")

    for engine in _engines():
        out = _grep(monkeypatch, tmp_path, engine=engine, pattern="needle")
        assert len(out.splitlines()) == limit + int(truncated)
        assert ("truncated" in out) is truncated


def test_ripgrep_respects_hidden_and_gitignore(monkeypatch, tmp_path):
    """`--glob` 会盖掉 .gitignore / 隐藏规则，所以文件名过滤不能交给 rg 的命令行。"""
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    if not shutil.which("git"):
        pytest.skip("git not installed")
    (tmp_path / "kept.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".hidden.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)

    for file_pattern in ("*", "*.py"):
        out = _grep(monkeypatch, tmp_path, engine="ripgrep", pattern="needle", file_pattern=file_pattern)
        assert [name for name, _, _ in _names(out)] == ["kept.py"]


def test_output_is_sorted(monkeypatch, tmp_path):
    """命中顺序由工具自己决定，不随 ripgrep 并行遍历变化。"""
    (tmp_path / "b.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("needle\nneedle\n", encoding="utf-8")

    for engine in _engines():
        records = _names(_grep(monkeypatch, tmp_path, engine=engine, pattern="needle"))
        assert records == sorted(records, key=lambda record: (record[0], record[1]))


@pytest.mark.parametrize("engine", ["python", "ripgrep"])
def test_bad_pattern_raises(monkeypatch, tmp_path, engine):
    if engine == "ripgrep" and not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    (tmp_path / "a.py").write_text("needle\n", encoding="utf-8")

    with pytest.raises(Exception):
        _grep(monkeypatch, tmp_path, engine=engine, pattern="(")
