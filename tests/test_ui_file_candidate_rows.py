"""@ 文件候选行：左列仅条目名（目录带 /），右列完整相对路径（淡灰），名称列定宽对齐。"""
from core.tui.theme import SECONDARY_TEXT
from core.tui.widgets import _file_display_name, _file_rows


def test_display_name_uses_leaf_and_marks_directories():
    assert _file_display_name("core/tui/widgets.py") == "widgets.py"
    assert _file_display_name("core/tui/") == "tui/"
    assert _file_display_name("main.py") == "main.py"


def test_file_rows_show_leaf_name_then_relative_path():
    candidates = ["core/tui/", "core/tui/widgets.py", "main.py"]

    rows = _file_rows(candidates)

    assert [value for value, _row in rows] == candidates
    assert [row.plain.split() for _value, row in rows] == [
        ["tui/", "core/tui/"],
        ["widgets.py", "core/tui/widgets.py"],
        ["main.py", "main.py"],
    ]
    # 名称列定宽：路径列起点一致
    assert {row.plain.rindex(value) for value, row in rows} == {14}
    # 第二段路径用次级文字色，与 / 指令说明一致
    for value, row in rows:
        assert row.plain.endswith(value)
        assert row.spans[-1].end == len(row.plain)
        assert str(row.spans[-1].style) == SECONDARY_TEXT


def test_file_rows_empty_candidates():
    assert _file_rows([]) == []
