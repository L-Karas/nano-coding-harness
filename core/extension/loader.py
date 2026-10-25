"""扩展加载器：读取 .harness/extensions 下的扩展包，按清单门槛逐个原子注册。

每个扩展是一个含 extension.json 与 __init__.py 的目录；register(api) 通过
ExtensionAPI 缓冲注册，正常返回后一次性提交到派发器，失败则整体作废（原子性）。
"""
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Iterable, NamedTuple

import core.extension.dispatcher as dispatcher
import core.extension.registry as registry
from core.config import EXTENSION_DIR
from core.extension.api import ExtensionAPI
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)

_LOADED = False


class _ManifestState(NamedTuple):
    """清单扫描结果：reason 为 None 表示启用；description 为可展示描述。"""

    reason: str | None
    description: str


def load_extensions(directory: Path | None = None) -> None:
    """加载全部扩展；进程内幂等，重复调用直接返回。"""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    target = directory or EXTENSION_DIR
    if not target.exists():
        return

    def _record(
        folder: Path,
        description: str,
        *,
        enabled: bool,
        detail: str = "",
        events: Iterable[str] = (),
    ) -> None:
        """登记本目录的扫描结果；外部扩展来源固定为 user。"""
        registry.record(folder.name, "user", enabled=enabled, detail=detail,
                        events=events, description=description)

    # todo: 回调顺序与扩展文件夹名相关，应由用户控制
    for folder in sorted(target.iterdir()):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        manifest = _manifest_state(folder)
        if manifest.reason is not None:
            _record(folder, manifest.description, enabled=False, detail=manifest.reason)
            continue
        try:
            events = _load_extension(folder)
        except Exception:
            _LOGGER.exception(f"[Extension] 加载失败，已隔离: {folder.name}")
            _record(folder, manifest.description, enabled=False, detail="load failed")
            continue
        if events is None:
            _record(folder, manifest.description, enabled=False,
                    detail="missing register(api)")
        else:
            _record(folder, manifest.description, enabled=True, events=events)


def reset() -> None:
    """清空加载状态，供测试隔离使用。"""
    global _LOADED
    _LOADED = False


def _manifest_state(folder: Path) -> _ManifestState:
    """按 manifest 门槛判断是否加载，返回原因与描述；reason=None 表示通过。

    描述在清单解析成功后读取，因此 disabled 条目也能展示；缺清单 / 非法清单返回空串。
    """
    manifest = folder / "extension.json"
    if not manifest.exists():
        _LOGGER.info(f"[Extension] 缺少 extension.json，跳过: {folder.name}")
        return _ManifestState("missing extension.json", "")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        _LOGGER.error(f"[Extension] extension.json 解析失败，跳过: {folder.name}")
        return _ManifestState("invalid extension.json", "")
    if not isinstance(data, dict):
        _LOGGER.error(f"[Extension] extension.json 不是对象，跳过: {folder.name}")
        return _ManifestState("invalid extension.json", "")
    description = _manifest_description(data.get("description"), folder.name)
    if data.get("enabled") is not True:
        _LOGGER.debug(f"[Extension] enabled 不是严格 True，跳过: {folder.name}")
        return _ManifestState("disabled", description)
    return _ManifestState(None, description)


def _manifest_description(value: object, folder_name: str) -> str:
    """可选展示描述：仅接受字符串；空串 / 非字符串视为无描述，连续空白折叠成单空格。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        _LOGGER.warning(f"[Extension] description 不是字符串，忽略: {folder_name}")
        return ""
    return " ".join(value.split())


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
