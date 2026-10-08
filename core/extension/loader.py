"""扩展加载器：读取 .harness/extensions 下的扩展包，按清单门槛逐个原子注册。

每个扩展是一个含 extension.json 与 __init__.py 的目录；register(api) 通过
ExtensionAPI 缓冲注册，正常返回后一次性提交到派发器，失败则整体作废（原子性）。
"""
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from core.config import EXTENSION_DIR
from core.extension.api import ExtensionAPI
import core.extension.dispatcher as dispatcher
from core.log.log import get_logger

_LOGGER = get_logger(__name__)

_LOADED = False


def load_extensions(directory: Path | None = None) -> None:
    """加载全部扩展；进程内幂等，重复调用直接返回。"""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    target = directory or EXTENSION_DIR
    if not target.exists():
        return

    for folder in sorted(target.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        if _read_enabled(folder):
            _load_extension(folder)


def reset() -> None:
    """清空加载状态，供测试隔离使用。"""
    global _LOADED
    _LOADED = False


def _read_enabled(folder: Path) -> bool:
    """按 manifest 门槛决定是否加载；enabled 必须严格 is True。"""
    manifest = folder / "extension.json"
    if not manifest.exists():
        _LOGGER.info(f"[Extension] 缺少 extension.json，跳过: {folder.name}")
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        _LOGGER.error(f"[Extension] extension.json 解析失败，跳过: {folder.name}")
        return False
    if not isinstance(data, dict) or data.get("enabled") is not True:
        _LOGGER.debug(f"[Extension] enabled 不是严格 True，跳过: {folder.name}")
        return False
    return True


def _load_extension(folder: Path) -> None:
    """按包加载单个扩展；register 失败时回滚，不留下半个扩展的注册。"""
    module_name = f"nano_extension_{folder.name}"
    try:
        spec = spec_from_file_location(
            module_name,
            folder / "__init__.py",
            submodule_search_locations=[str(folder)],
        )
        module = module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        register = getattr(module, "register", None)
        if not callable(register):
            _LOGGER.info(f"[Extension] 缺少可调用 register，跳过: {folder.name}")
            return

        api = ExtensionAPI(module_name)
        register(api)
        for event, callback in api.commit():
            dispatcher.add(event, callback, module_name)
    except Exception:
        _LOGGER.exception(f"[Extension] 加载失败，已隔离: {folder.name}")
        sys.modules.pop(module_name, None)
