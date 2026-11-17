"""read_file / edit_file 与 write_file 一致：目录路径给出显式 IsADirectoryError。"""
import asyncio

import pytest

from core.runtime_context import ToolContext
from core.tools import ToolPool
from core.tools.base_tools.edit import EditFile
from core.tools.base_tools.read import ReadFile


def _execute(name, args, cwd, async_call):
    pool = ToolPool({"read_file": ReadFile, "edit_file": EditFile})
    tctx = ToolContext(cwd=cwd)
    if async_call:
        return asyncio.run(pool.execute(name, args, tctx))
    return pool.execute_sync(name, args, tctx)


@pytest.mark.parametrize("async_call", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("name,args", [
    ("read_file", {"path": "folder"}),
    ("read_file", {"path": "folder.png"}),
    ("edit_file", {"path": "folder", "old_text": "a", "new_text": "b"}),
])
def test_directory_target_is_rejected(tmp_path, async_call, name, args):
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder.png").mkdir()

    result = _execute(name, args, tmp_path, async_call)

    assert result.is_error is True
    assert result.content.endswith(f"is a directory: {args['path']}")
