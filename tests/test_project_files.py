"""list_project_files 剪枝规则测试:隐藏/虚拟环境/缓存目录与临时文件不进结果"""
from core.tui import utils


def test_list_project_files_skips_noise(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("print('hi')", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "mod.md").write_text("doc", encoding="utf-8")
    (root / "keep.py~").write_text("junk")     # ~ 结尾的编辑器临时文件
    (root / "x.tmp").write_text("junk")        # .tmp 临时文件
    (root / ".env").write_text("SECRET=1")     # 点文件
    (root / "bin.dat").write_bytes(b"\xff\xfe\x00")  # 二进制文件也应列出
    for d in (".git", ".venv", "venv", "__pycache__", "build", "dist", "node_modules"):
        (root / d).mkdir()
        (root / d / "inner.txt").write_text("junk", encoding="utf-8")

    files = utils.list_project_files(str(root))

    assert files == ["bin.dat", "main.py", "sub/mod.md"]
