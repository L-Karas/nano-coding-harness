"""扩展登记表：记录启动时扫描 / 注册到的扩展及其来源与加载状态。

loader 登记外部扩展（含未启用原因），builtin 注册入口登记内置扩展；TUI 的 /extensions
只读这张表，不重新解析清单。进程内单例，reset() 供测试隔离与 reset_extensions() 使用。
"""
from dataclasses import dataclass
from typing import Iterable, Literal

Origin = Literal["user", "builtin"]


@dataclass(frozen=True)
class ExtensionInfo:
    """一条扩展登记：user = .harness/extensions 下的文件夹；builtin = 内置模块。

    description 为清单里的可选展示描述（空 = 不显示）。"""

    name: str
    origin: Origin
    enabled: bool
    detail: str = ""
    events: tuple[str, ...] = ()
    description: str = ""


_ENTRIES: list[ExtensionInfo] = []


def record(
        name: str,
        origin: Origin,
        *,
        enabled: bool,
        detail: str = "",
        events: Iterable[str] = (),
        description: str = "",
) -> None:
    """登记一条扩展；同名同源原地替换，不同来源同名各自保留。"""
    info = ExtensionInfo(
        name=name,
        origin=origin,
        enabled=enabled,
        detail=detail,
        events=_unique_events(events),
        description=description,
    )
    for index, existing in enumerate(_ENTRIES):
        if (existing.origin, existing.name) == (origin, name):
            _ENTRIES[index] = info
            return
    _ENTRIES.append(info)


def list_extensions() -> list[ExtensionInfo]:
    """返回排序后的快照：builtin 在前，同来源按名称（忽略大小写）排序。"""
    return sorted(_ENTRIES, key=lambda entry: (entry.origin != "builtin", entry.name.casefold()))


def reset() -> None:
    """清空登记表，供测试隔离使用。"""
    _ENTRIES.clear()


def _unique_events(events: Iterable[str]) -> tuple[str, ...]:
    """事件名按首次出现顺序去重（保留注册顺序）。"""
    seen: set[str] = set()
    unique: list[str] = []
    for event in events:
        if event not in seen:
            seen.add(event)
            unique.append(event)
    return tuple(unique)
