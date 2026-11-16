"""core/context/memory 的记忆作用域与目录行为测试。"""
import json

import pytest

import core.config as config
from core.context.memory.memory import MemoryManager
from core.tools.extra_tools.memory import SaveMemory


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """记忆目录隔离到 tmp_path；local 目录名沿用工作目录格式化结果。"""
    memory_root = tmp_path / ".memory"
    monkeypatch.setattr(config.PATHS, "LOCAL_MEMORY_DIR",
                        memory_root / config.format_workdir_name(config.WORKDIR))
    monkeypatch.setattr(config.PATHS, "GLOBAL_MEMORY_DIR", memory_root / "global-memories")
    return MemoryManager()


def test_load_memories_returns_empty_when_dirs_missing(manager):
    assert manager.load_memories() == []


def test_save_memory_tool_scope_defaults_to_local():
    parameters = SaveMemory.to_openai_tool()["function"]["parameters"]
    assert parameters["properties"]["scope"]["default"] == "local"
    assert "scope" not in parameters.get("required", [])


def test_create_local_memory_writes_to_local_dir(manager):
    message, memory_id = manager.create_memory("t", "c", "project")

    path = config.PATHS.LOCAL_MEMORY_DIR / f"{memory_id}.json"
    assert message == f"[Memory created] id: {memory_id} (scope: local)"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "id": memory_id, "title": "t", "content": "c", "mem_type": "project"}
    assert not (config.PATHS.GLOBAL_MEMORY_DIR / f"{memory_id}.json").exists()


def test_create_global_memory_writes_to_global_dir(manager):
    message, memory_id = manager.create_memory("t", "c", "user", scope="global")

    path = config.PATHS.GLOBAL_MEMORY_DIR / f"{memory_id}.json"
    assert message == f"[Memory created] id: {memory_id} (scope: global)"
    assert path.is_file()
    assert not (config.PATHS.LOCAL_MEMORY_DIR / f"{memory_id}.json").exists()


def test_load_memories_merges_global_before_local(manager):
    manager.create_memory("l", "lc", "project")
    manager.create_memory("g", "gc", "user", scope="global")

    memories = manager.load_memories()

    assert [(m.title, m.scope) for m in memories] == [("g", "global"), ("l", "local")]


def test_load_memories_prefers_local_on_duplicate_id(manager):
    for directory, content in ((config.PATHS.GLOBAL_MEMORY_DIR, "global"),
                               (config.PATHS.LOCAL_MEMORY_DIR, "local")):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "memory-dup.json").write_text(
            json.dumps({"id": "memory-dup", "title": "t", "content": content,
                        "mem_type": "project"}),
            encoding="utf-8")

    memories = manager.load_memories()

    assert len(memories) == 1
    assert (memories[0].content, memories[0].scope) == ("local", "local")


def test_load_ignores_scope_written_in_json(manager):
    config.PATHS.LOCAL_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PATHS.LOCAL_MEMORY_DIR / "memory-stale.json"
    path.write_text(json.dumps({"id": "memory-stale", "title": "t", "content": "c",
                                "mem_type": "project", "scope": "global"}),
                    encoding="utf-8")

    assert manager.load_memory("memory-stale").scope == "local"


def test_load_memory_searches_both_scopes(manager):
    _, global_id = manager.create_memory("g", "gc", "user", scope="global")
    _, local_id = manager.create_memory("l", "lc", "project")

    fresh = MemoryManager()
    assert fresh.load_memory(global_id).scope == "global"
    assert fresh.load_memory(local_id).scope == "local"
    assert fresh.load_memory("memory-missing") is None


def test_delete_memory_searches_both_scopes(manager):
    _, global_id = manager.create_memory("g", "gc", "user", scope="global")
    _, local_id = manager.create_memory("l", "lc", "project")

    assert manager.delete_memory(local_id) is True
    assert not (config.PATHS.LOCAL_MEMORY_DIR / f"{local_id}.json").exists()
    assert manager.delete_memory(global_id) is True
    assert not (config.PATHS.GLOBAL_MEMORY_DIR / f"{global_id}.json").exists()
    assert manager.delete_memory("memory-missing") is False


def test_delete_memory_removes_duplicate_copies(manager):
    for directory in (config.PATHS.GLOBAL_MEMORY_DIR, config.PATHS.LOCAL_MEMORY_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "memory-dup.json").write_text(
            json.dumps({"id": "memory-dup", "title": "t", "content": "c",
                        "mem_type": "project"}),
            encoding="utf-8")

    assert manager.delete_memory("memory-dup") is True
    assert not (config.PATHS.LOCAL_MEMORY_DIR / "memory-dup.json").exists()
    assert not (config.PATHS.GLOBAL_MEMORY_DIR / "memory-dup.json").exists()
