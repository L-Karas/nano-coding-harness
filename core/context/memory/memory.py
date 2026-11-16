"""长期记忆：按工作目录隔离（local）或跨工作目录共享（global）。

记忆的 scope 由所在目录决定、不写入 JSON：`.memory/<格式化工作目录>/` 对应 local，
`.memory/global-memories/` 对应 global。
"""
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

from core.config import PATHS
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)

# 记忆存储作用域：local 仅当前工作目录，global 跨工作目录共享。
MemoryScope = Literal["local", "global"]


@dataclass
class Memory:
    """一条持久化记忆。"""

    id: str
    title: str
    content: str
    mem_type: Literal["user", "feedback", "project", "reference"]
    scope: MemoryScope


class MemoryManager:
    """记忆的创建 / 加载 / 删除管理器，负责内存缓存与磁盘同步。"""

    def __init__(self) -> None:
        """初始化空的内存记忆缓存与重复 id 告警记录。"""
        self.memories: dict[str, Memory] = {}
        self._warned_duplicate_ids: set[str] = set()

    @staticmethod
    def _get_id() -> str:
        """生成短记忆 id。

        Returns:
            形如 ``memory-<8 位十六进制>`` 的标识。
        """
        return f"memory-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _dir_for_scope(scope: MemoryScope) -> Path:
        """返回 scope 对应的记忆目录。

        Args:
            scope: local（仅当前工作目录）/ global（跨工作目录共享）。

        Returns:
            记忆目录。

        Raises:
            ValueError: scope 不是 local / global。
        """
        if scope == "local":
            return PATHS.LOCAL_MEMORY_DIR
        if scope == "global":
            return PATHS.GLOBAL_MEMORY_DIR
        raise ValueError(f"unknown scope: {scope!r}")

    @staticmethod
    def _load_memory_file(path: Path, scope: MemoryScope) -> Memory:
        """读取单个记忆文件，并用所在目录覆盖 scope。

        Args:
            path: 记忆 JSON 文件。
            scope: 文件所在目录对应的作用域。

        Returns:
            记忆对象。
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("scope", None)  # 目录是唯一事实来源：旧文件若带 scope 也不采信
        memory = Memory(**data, scope=scope)
        if path.stem != memory.id:
            _LOGGER.warning(f"Memory file {path} contains id {memory.id}.")
        return memory

    def create_memory(
        self,
        title: str,
        content: str,
        mem_type: Literal["user", "feedback", "project", "reference"],
        scope: MemoryScope = "local",
    ) -> tuple[str, str]:
        """创建记忆并落盘。

        Args:
            title: 记忆标题。
            content: 记忆正文。
            mem_type: 记忆类型（user / feedback / project / reference）。
            scope: 存储作用域（local 仅当前工作目录 / global 跨工作目录共享）。

        Returns:
            (创建结果提示, 新记忆 id)。
        """
        memory = Memory(
            id=self._get_id(),
            title=title,
            content=content,
            mem_type=mem_type,
            scope=scope,
        )
        self.memories[memory.id] = memory
        return self._save_memory(memory), memory.id

    def _save_memory(self, memory: Memory) -> str:
        """把单条记忆写入 scope 对应的目录。

        Args:
            memory: 待保存的记忆。

        Returns:
            创建成功提示（含记忆 id 与 scope）。

        Raises:
            Exception: 文件写入失败。
        """
        directory = self._dir_for_scope(memory.scope)
        path = directory / f"{memory.id}.json"
        data = asdict(memory)
        data.pop("scope")  # scope 由所在目录决定，不落盘
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=4, ensure_ascii=False))
            return f"[Memory created] id: {memory.id} (scope: {memory.scope})"
        except Exception as e:
            _LOGGER.error(f"Memory save error: {e}")
            raise Exception(f"Memory create error: {e}")

    def load_memory(self, id: str) -> Optional[Memory]:
        """按 id 加载记忆；优先命中内存缓存，否则依次查 local、global 目录。

        Args:
            id: 记忆 id。

        Returns:
            记忆对象；两处都不存在时返回 None。
        """
        if id in self.memories:
            return self.memories[id]

        for scope in ("local", "global"):
            path = self._dir_for_scope(scope) / f"{id}.json"
            if path.exists():
                return self._load_memory_file(path, scope)

        _LOGGER.info(f"Memory {id} load error: memory {id} does not exist.")
        return None

    def load_memories(self) -> list[Memory]:
        """从 local / global 目录重新加载全部记忆并刷新内存缓存。

        global 先加载、local 后加载：返回列表 global 在前；同 id 出现在两处时
        local 覆盖 global，且同一 id 只告警一次。

        Returns:
            已加载的记忆列表；两个目录都不存在或都没有 JSON 时返回空列表。
        """
        self.memories.clear()
        for scope in ("global", "local"):
            directory = self._dir_for_scope(scope)
            if not directory.exists():
                continue
            for path in directory.glob("*.json"):
                try:
                    memory = self._load_memory_file(path, scope)
                except Exception as e:
                    _LOGGER.error(f"Memory {path} load error: {e}")
                    continue
                if memory.id in self.memories:
                    if memory.id not in self._warned_duplicate_ids:
                        _LOGGER.warning(
                            f"Memory {memory.id} exists in both scopes; using {scope} from {path}.")
                        self._warned_duplicate_ids.add(memory.id)
                self.memories[memory.id] = memory

        _LOGGER.info(f"Memory load success: {len(self.memories)} memories.")
        return list(self.memories.values())

    def delete_memory(self, id: str) -> bool:
        """删除指定记忆：删除 local / global 两处的同名文件，并清掉内存缓存。

        Args:
            id: 记忆 id。

        Returns:
            是否删除成功；两处都不存在或删除失败时返回 False。
        """
        found = False
        for scope in ("local", "global"):
            path = self._dir_for_scope(scope) / f"{id}.json"
            if not path.exists():
                continue
            found = True
            try:
                path.unlink()
            except Exception as e:
                _LOGGER.error(f"Memory {id} delete error: {e}")
                return False

        if not found:
            _LOGGER.info(f"Memory {id} delete error: memory {id} does not exist.")
            return False
        self.memories.pop(id, None)
        return True


MEMORY_MANAGER = MemoryManager()


def run_save_memory(title: str, content: str,
                    mem_type: Literal["user", "feedback", "project", "reference"],
                    scope: MemoryScope = "local") -> tuple[str, str]:
    """创建记忆并落盘（工具层入口）。

    Args:
        title: 记忆标题。
        content: 记忆正文。
        mem_type: 记忆类型（user / feedback / project / reference）。
        scope: 存储作用域（local 仅当前工作目录 / global 跨工作目录共享）。

    Returns:
        (创建结果提示, 新记忆 id)。
    """
    return MEMORY_MANAGER.create_memory(title, content, mem_type, scope=scope)
