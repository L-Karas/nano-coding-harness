"""
Memory module


"""
import json
import uuid
from dataclasses import dataclass, asdict
from typing import Literal, Optional

from core.config import PATHS
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)


@dataclass
class Memory:
    """一条持久化记忆。"""

    id: str
    title: str
    content: str
    mem_type: Literal["user", "feedback", "project", "reference"]


class MemoryManager:
    """记忆的创建 / 加载 / 删除管理器，负责内存缓存与磁盘同步。"""

    def __init__(self) -> None:
        """初始化空的内存记忆缓存。"""
        self.memories: dict[str, Memory] = {}

    @staticmethod
    def _get_id() -> str:
        """生成短记忆 id。

        Returns:
            形如 ``memory-<8 位十六进制>`` 的标识。
        """
        return f"memory-{uuid.uuid4().hex[:8]}"

    def create_memory(
        self,
        title: str,
        content: str,
        mem_type: Literal["user", "feedback", "project", "reference"],
    ) -> tuple[str, str]:
        """创建记忆并落盘。

        Args:
            title: 记忆标题。
            content: 记忆正文。
            mem_type: 记忆类型（user / feedback / project / reference）。

        Returns:
            (创建结果提示, 新记忆 id)。
        """
        memory = Memory(
            id=self._get_id(),
            title=title,
            content=content,
            mem_type=mem_type,
        )
        self.memories[memory.id] = memory
        return self._save_memory(memory), memory.id

    def _save_memory(self, memory: Memory) -> str:
        """把单条记忆写入 MEMORY_DIR。

        Args:
            memory: 待保存的记忆。

        Returns:
            创建成功提示（含记忆 id）。

        Raises:
            Exception: 文件写入失败。
        """
        path = PATHS.MEMORY_DIR / f"{memory.id}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(asdict(memory), indent=4, ensure_ascii=False))
            return f"[Memory created] id: {memory.id}"
        except Exception as e:
            _LOGGER.error(f"Memory save error: {e}")
            raise Exception(f"Memory create error: {e}")

    def load_memory(self, id: str) -> Optional[Memory]:
        """按 id 加载记忆；优先命中内存缓存，否则读文件。

        Args:
            id: 记忆 id。

        Returns:
            记忆对象；不存在时返回 None。
        """
        if id in self.memories:
            return self.memories[id]

        path = PATHS.MEMORY_DIR / f"{id}.json"
        if not path.exists():
            _LOGGER.info(f"Memory {id} load error: memory {id} does not exist.")
            return None

        memory = Memory(**json.loads(path.read_text(encoding="utf-8")))
        return memory

    def load_memories(self) -> list[Memory]:
        """从 MEMORY_DIR 重新加载全部记忆并刷新内存缓存。

        Returns:
            已加载的记忆列表；目录不存在时返回空列表。
        """
        if not PATHS.MEMORY_DIR.exists():
            return []

        self.memories.clear()
        for path in PATHS.MEMORY_DIR.glob("*.json"):
            try:
                memory = Memory(**json.loads(path.read_text(encoding="utf-8")))
                self.memories[memory.id] = memory
            except Exception as e:
                _LOGGER.error(f"Memory {path} load error: {e}")

        _LOGGER.info(f"Memory load success: {len(self.memories)} memories.")
        return list(self.memories.values())

    def delete_memory(self, id: str) -> bool:
        """删除指定记忆的文件与内存缓存。

        Args:
            id: 记忆 id。

        Returns:
            是否删除成功；记忆不存在或删除失败时返回 False。
        """
        path = PATHS.MEMORY_DIR / f"{id}.json"
        if id not in self.memories or not path.exists():
            _LOGGER.info(f"Memory {id} delete error: memory {id} does not exist.")
            return False

        try:
            path.unlink(missing_ok=True)
            self.memories.pop(id)
            return True
        except Exception as e:
            _LOGGER.error(f"Memory {id} delete error: {e}")
            return False


MEMORY_MANAGER = MemoryManager()


def run_save_memory(title: str, content: str,
                    mem_type: Literal["user", "feedback", "project", "reference"]) -> tuple[str, str]:
    """创建记忆并落盘（工具层入口）。

    Args:
        title: 记忆标题。
        content: 记忆正文。
        mem_type: 记忆类型（user / feedback / project / reference）。

    Returns:
        (创建结果提示, 新记忆 id)。
    """
    return MEMORY_MANAGER.create_memory(title, content, mem_type)
