"""文件落盘与换行风格：write / edit 共用同一套语义。

`atomic_write` 负责「临时文件 → 保留权限 → fsync → os.replace」与失败 / 中断时的清理；
`normalize_newlines` / `match_eol` 固定「读进来按 \n 处理，写回去按原文件风格」的约定。
"""
import asyncio
import os
import stat
from contextlib import suppress
from pathlib import Path

import aiofiles

from core.logger import get_logger
from core.runtime_context import ToolContext

_LOGGER = get_logger(__name__)
_POSIX = os.name == "posix"


def normalize_newlines(text: str) -> str:
    """CRLF → LF；单独出现的 \\r 不动（与 read_file 的换行归一保持一致）。"""
    return text.replace("\r\n", "\n")


def match_eol(text: str, source: str | None) -> str:
    """把 LF 文本改成 source 文件的换行风格；source 为 None（新建 / 读不到）时保持 LF。

    CRLF 行占多数就用 CRLF，平局与纯 LF 都算 LF（与 edit_file 的旧行为一致）。
    """
    if source is None:
        return text
    crlf = source.count("\r\n")
    return text.replace("\n", "\r\n") if crlf > source.count("\n") - crlf else text


def _create_temp(path: Path) -> Path:
    """在目标目录内建独占临时文件。

    用 `open(..., "x")` 而不是 `mkstemp`：前者按 0o666&~umask 建文件，后者固定 0600，
    会让新建的产物对同机其他用户不可读。
    """
    for _ in range(100):
        tmp = path.with_name(f".{path.name}.{os.urandom(6).hex()}.tmp")
        try:
            open(tmp, "x").close()
            return tmp
        except FileExistsError:
            continue
    raise FileExistsError(f"cannot create a temp file next to {path}")


def _preserve_mode(src: Path, tmp: Path) -> None:
    """覆盖已有文件时把临时文件的 mode 改成原文件的（0755 脚本不能悄悄变成 0644/0600）。

    Windows 只有只读位，交给 os.replace 的报错处理即可。
    """
    if not _POSIX:
        return
    try:
        os.chmod(tmp, stat.S_IMODE(src.stat().st_mode))
    except FileNotFoundError:  # 新建文件：权限由 umask 决定
        pass
    except OSError as e:  # SMB / FUSE 上 chmod 常失败：不阻断写入，但要留痕，权限可能被放宽
        _LOGGER.warning(f"[File IO] keep mode failed for {src}: {e}")


async def atomic_write(path: Path, text: str, tctx: ToolContext | None = None) -> None:
    """把 text 原样写进 path（不做换行翻译，换行风格由调用方决定）。

    目标文件要么是旧内容、要么是新内容：进程中途被杀不会留下半截文件。
    """
    tmp = None
    try:
        tmp = _create_temp(path)
        if tctx:
            tctx.raise_if_cancelled()
        async with aiofiles.open(tmp, mode="w", encoding="utf-8", newline="") as f:
            await f.write(text)
            await f.flush()
            # 写完才改 mode：原文件若只读（0444），先 chmod 会让这里的 open("w") 被拒
            _preserve_mode(path, tmp)
            await asyncio.to_thread(os.fsync, f.fileno())
        if tctx:
            tctx.raise_if_cancelled()
        os.replace(tmp, path)
    except OSError as e:
        # 不把临时文件名漏给模型（Windows 的 PermissionError 会把 tmpXXX 带进 str(e)）
        raise OSError(f"cannot write {path}: {e.strerror or e}") from e
    finally:
        # 成功 replace 后这里已是 no-op；失败 / 中断时清掉残留。
        # 用 suppress 而非 missing_ok：Windows 上 unlink 可能报 PermissionError，不能顶掉正在传播的中断。
        if tmp is not None:
            with suppress(OSError):
                tmp.unlink()
