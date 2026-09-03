"""
Memory module


"""
import json
import uuid
from dataclasses import dataclass, asdict
from typing import Literal, Optional

from src.config import MEMORY_DIR
from src.log import get_logger

_LOGGER = get_logger(__name__)


@dataclass
class Memory:
    id: str
    title: str
    content: str
    mem_type: Literal["user", "feedback", "project", "reference"]


class MemoryManager:

    def __init__(self):
        self.memories: dict[str, Memory] = {}
        if not MEMORY_DIR.exists():
            MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _get_id():
        return f"memory-{uuid.uuid4().hex[:8]}"

    def create_memory(self, title: str, content: str,
                      mem_type: Literal["user", "feedback", "project", "reference"]) -> str:
        memory = Memory(id=self._get_id(), title=title, content=content, mem_type=mem_type)
        self.memories[memory.id] = memory
        return self._save_memory(memory), memory.id

    def _save_memory(self, memory: Memory) -> str:
        path = MEMORY_DIR / f"{memory.id}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(asdict(memory), indent=4, ensure_ascii=False))
            return f"[Memory created] id: {memory.id}"
        except Exception as e:
            _LOGGER.error(f"Memory save error: {e}")
            return f"[Memory create error] info: {e}"

    def load_memory(self, id: str) -> Optional[Memory]:
        if id in self.memories:
            return self.memories[id]

        path = MEMORY_DIR / f"{id}.json"
        if not path.exists():
            _LOGGER.info(f"Memory {id} load error: memory {id} does not exist.")
            return None

        memory = Memory(**json.loads(path.read_text(encoding="utf-8")))
        return memory

    def load_memories(self) -> list[Memory]:
        if not MEMORY_DIR.exists():
            return []

        self.memories.clear()
        for path in MEMORY_DIR.glob("*.json"):
            try:
                memory = Memory(**json.loads(path.read_text(encoding="utf-8")))
                self.memories[memory.id] = memory
            except Exception as e:
                _LOGGER.error(f"Memory {path} load error: {e}")

        _LOGGER.info(f"Memory load success: {len(self.memories)} memories.")
        return list(self.memories.values())

    def delete_memory(self, id: str) -> bool:
        path = MEMORY_DIR / f"{id}.json"
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


def run_save_memory(title: str, content: str, mem_type: Literal["user", "feedback", "project", "reference"]):
    return MEMORY_MANAGER.create_memory(title, content, mem_type)


if __name__ == '__main__':
    manager = MemoryManager()
    _, mid = manager.create_memory(
        title="always_respond_in_chinese",
        content="无论用户使用何种语言输入（中文、英文或其他语言），所有回答都必须使用中文。这是强制性的语言偏好设置。",
        mem_type="user"
    )
    manager.create_memory(
        title="module_docstring_style",
        content="为模块添加文档注释时，仅使用模块级 docstring（放在文件开头的三重引号字符串），不需要为每个变量添加行内注释。",
        mem_type="user"
    )
    # self-check: create -> load -> delete round trip
    assert len(manager.load_memories()) >= 2, "seeded memories must load back"
    assert manager.load_memory(mid).title == "always_respond_in_chinese"
    assert manager.delete_memory(mid)
    assert not manager.delete_memory(mid)
    print("memory self-check OK")
