"""工具调用卡参数展示：dict 每参一行 `key: value`，非 dict 保留旧回退。"""
from core.tui import render


def test_dict_renders_one_argument_per_line():
    assert render._format_args({"command": "ls -la", "timeout": 30}) == "command: ls -la\ntimeout: 30"


def test_string_value_raw_without_json_quotes():
    assert render._format_args({"path": "/tmp/a b", "text": "你好"}) == "path: /tmp/a b\ntext: 你好"


def test_multiline_string_escaped_to_single_line():
    assert render._format_args({"content": "line1\nline2"}) == "content: line1\\nline2"


def test_scalars_use_json_literals():
    assert render._format_args({"flag": True, "off": False, "empty": None, "ratio": 1.5}) == (
        "flag: true\noff: false\nempty: null\nratio: 1.5"
    )


def test_nested_containers_inline_compact_json():
    assert render._format_args({"options": {"a": 1, "b": [1, 2]}}) == 'options: {"a": 1, "b": [1, 2]}'


def test_empty_dict():
    assert render._format_args({}) == "{}"


def test_non_dict_fallback_unchanged():
    assert render._format_args("not json") == "not json"
    assert render._format_args([1, 2]) == "[\n  1,\n  2\n]"
