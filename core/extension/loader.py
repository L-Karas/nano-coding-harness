"""扩展加载器：读取 .harness/extensions 下的扩展包，按清单门槛逐个原子注册。

每个扩展是一个含 extension.json 与 __init__.py 的目录；register(api) 通过
ExtensionAPI 缓冲注册，正常返回后一次性提交到派发器，失败则整体作废（原子性）。
"""
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import core.extension.dispatcher as dispatcher
import core.extension.registry as registry
from core.config import EXTENSION_DIR
from core.extension.api import ExtensionAPI
from core.shared.logger import get_logger

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

    # todo: 回调顺序与扩展文件夹名相关，应由用户控制
    for folder in sorted(target.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        reason = _manifest_reason(folder)
        if reason is not None:
            registry.record(folder.name, "user", enabled=False, detail=reason)
            continue
        try:
            events = _load_extension(folder)
        except Exception:
            _LOGGER.exception(f"[Extension] 加载失败，已隔离: {folder.name}")
            registry.record(folder.name, "user", enabled=False, detail="load failed")
            continue
        if events is None:
            registry.record(
                folder.name, "user", enabled=False, detail="missing register(api)")
        else:
            registry.record(folder.name, "user", enabled=True, events=events)


def reset() -> None:
    """清空加载状态，供测试隔离使用。"""
    global _LOADED
    _LOADED = False


def _manifest_reason(folder: Path) -> str | None:
    """按 manifest 门槛判断是否加载；通过返回 None，否则返回未启用原因。"""
    manifest = folder / "extension.json"
    if not manifest.exists():
        _LOGGER.info(f"[Extension] 缺少 extension.json，跳过: {folder.name}")
        return "missing extension.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        _LOGGER.error(f"[Extension] extension.json 解析失败，跳过: {folder.name}")
        return "invalid extension.json"
    if not isinstance(data, dict):
        _LOGGER.error(f"[Extension] extension.json 不是对象，跳过: {folder.name}")
        return "invalid extension.json"
    if data.get("enabled") is not True:
        _LOGGER.debug(f"[Extension] enabled 不是严格 True，跳过: {folder.name}")
        return "disabled"
    return None


def _load_extension(folder: Path) -> list[str] | None:
    """按包加载单个扩展；register 失败时回滚并上抛，缺 register 返回 None。"""
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
            return None

        api = ExtensionAPI()
        register(api)
        registered = api.commit()
        for event, callback, stage, fail_closed in registered:
            dispatcher.add(
                event, callback, module_name, stage=stage, fail_closed=fail_closed)
        _LOGGER.info(f"[Extension] 加载成功: {folder.name}（注册 {len(registered)} 个回调）")
        return [event for event, _callback, _stage, _fail_closed in registered]
    except Exception:
        sys.modules.pop(module_name, None)
        raise
