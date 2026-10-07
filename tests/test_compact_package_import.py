"""回归：core.context.compact 包必须可导入（曾因 __init__ 残留已删除的 micro_compact 导入而失败）。"""


def test_compact_package_imports():
    import core.context.compact  # noqa: F401
