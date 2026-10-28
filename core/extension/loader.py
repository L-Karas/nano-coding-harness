"""扩展加载器：读取 .harness/extensions 下的扩展包，统一注册并按清单开关派发。

每个扩展是一个含 extension.json 与 __init__.py 的目录；register(api) 通过
ExtensionAPI 缓冲注册，正常返回后一次性提交到派发器，失败则整体作废（原子性）。
清单合法即加载注册；extension.json.enabled 只控制是否派发，便于 /extensions 即时启停。
"""
import contextlib
import json
import os
import stat
import sys
import tempfile
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Iterable, NamedTuple

import core.extension.dispatcher as dispatcher
import core.extension.registry as registry
from core.config import EXTENSION_DIR
from core.extension.api import ExtensionAPI
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)
_POSIX = os.name == "posix"

_LOADED = False
_FOLDERS: dict[str, Path] = {}
_LOAD_ERRORS: dict[str, str] = {}


class _ManifestState(NamedTuple):
    """清单扫描结果：reason 为 None 表示清单合法；data 为解析后的完整清单。"""

    reason: str | None
    description: str
    enabled: bool
    data: dict


def load_extensions(directory: Path | None = None) -> None:
    """加载全部扩展；进程内幂等，重复调用直接返回。

    清单合法的扩展无论 enabled 都会 import + register（保持注册顺序稳定），
    enabled 决定模块是否参与派发；清单缺失 / 非法仍按未加载处理。
    """
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    _FOLDERS.clear()
    _LOAD_ERRORS.clear()

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
        _FOLDERS[folder.name] = folder
        manifest = _manifest_state(folder)
        if manifest.reason is not None:
            _LOAD_ERRORS[folder.name] = manifest.reason
            _record(folder, manifest.description, enabled=False, detail=manifest.reason)
            continue
        try:
            events = _load_extension(folder)
        except Exception:
            _LOGGER.exception(f"[Extension] 加载失败，已隔离: {folder.name}")
            _LOAD_ERRORS[folder.name] = "load failed"
            _record(folder, manifest.description, enabled=False,
                    detail=_failure_detail(manifest.enabled, "load failed"))
            continue
        if events is None:
            _LOAD_ERRORS[folder.name] = "missing register(api)"
            _record(folder, manifest.description, enabled=False,
                    detail=_failure_detail(manifest.enabled, "missing register(api)"))
            continue
        dispatcher.set_module_enabled(_module_name(folder.name), manifest.enabled)
        _record(folder, manifest.description, enabled=manifest.enabled, events=events)


def reset() -> None:
    """清空加载状态，供测试隔离使用。"""
    global _LOADED
    _LOADED = False
    _FOLDERS.clear()
    _LOAD_ERRORS.clear()


def toggle_extension(name: str) -> tuple[bool, str, bool | None]:
    """翻转一个扩展的 enabled，并即时切换派发开关。

    Returns:
        tuple[是否成功写盘, 可直接展示的信息, 翻转后的配置值]；失败时第三项为 None。
    """
    folder = _FOLDERS.get(name)
    if folder is None:
        return False, f"Extension '{name}' was not loaded in this session; restart to load it", None
    state = _manifest_state(folder)
    if state.reason is not None:
        return False, f"Cannot toggle '{name}': {state.reason}", None
    enabled = not state.enabled
    ok, message = _apply_enabled(name, folder, state, enabled)
    return ok, message, enabled if ok else None


def _apply_enabled(name: str, folder: Path, state: _ManifestState, enabled: bool) -> tuple[bool, str]:
    """写入 enabled 并在写盘成功后切换派发；失败时不改变运行态。"""
    data = {**state.data, "enabled": bool(enabled)}
    try:
        _write_manifest(folder / "extension.json", data)
    except Exception as exc:  # 文件系统 / 编码错误都回滚，不能让 UI 动作抛穿
        _LOGGER.error(f"[Extension] 写入清单失败 {name}: {exc!r}")
        return False, f"Failed to save extension state: {exc}"

    entry = _entry(name)
    events = entry.events if entry else ()
    description = entry.description if entry else state.description

    if name not in _LOAD_ERRORS:
        dispatcher.set_module_enabled(_module_name(name), bool(enabled))
        registry.record(name, "user", enabled=bool(enabled), detail="",
                        events=events, description=description)
        action = "Enabled" if enabled else "Disabled"
        return True, f"{action} extension '{name}'"

    error = _LOAD_ERRORS.get(name, "load failed")
    registry.record(name, "user", enabled=False,
                    detail=error if enabled else f"disabled · {error}",
                    events=events, description=description)
    if enabled:
        return True, f"Set extension '{name}' to enabled, but it still fails to load: {error}"
    return True, f"Disabled extension '{name}'"


def _entry(name: str) -> registry.ExtensionInfo | None:
    """从登记表取当前 user 扩展条目；不存在时返回 None。"""
    return next((entry for entry in registry.list_extensions()
                 if entry.origin == "user" and entry.name == name), None)


def _manifest_state(folder: Path) -> _ManifestState:
    """解析清单：reason=None 表示合法；enabled 为严格布尔 true 的使用开关。

    描述与完整 data 在清单解析成功后一并返回；缺清单 / 非法清单返回空串与空 dict。
    """
    manifest = folder / "extension.json"
    if not manifest.exists():
        _LOGGER.info(f"[Extension] 缺少 extension.json，跳过: {folder.name}")
        return _ManifestState("missing extension.json", "", False, {})
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        _LOGGER.error(f"[Extension] extension.json 解析失败，跳过: {folder.name}")
        return _ManifestState("invalid extension.json", "", False, {})
    if not isinstance(data, dict):
        _LOGGER.error(f"[Extension] extension.json 不是对象，跳过: {folder.name}")
        return _ManifestState("invalid extension.json", "", False, {})
    description = _manifest_description(data.get("description"), folder.name)
    enabled = data.get("enabled") is True
    if not enabled:
        _LOGGER.debug(f"[Extension] enabled 不是严格 True，注册后不派发: {folder.name}")
    return _ManifestState(None, description, enabled, data)


def _manifest_description(value: object, folder_name: str) -> str:
    """可选展示描述：仅接受字符串；空串 / 非字符串视为无描述，连续空白折叠成单空格。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        _LOGGER.warning(f"[Extension] description 不是字符串，忽略: {folder_name}")
        return ""
    return " ".join(value.split())


def _failure_detail(enabled: bool, reason: str) -> str:
    """加载失败时的展示文案：配置禁用时同时保留用户关闭状态。"""
    return reason if enabled else f"disabled · {reason}"


def _module_name(folder_name: str) -> str:
    """扩展目录名 → 进程内模块名。"""
    return f"nano_extension_{folder_name}"


def _write_manifest(path: Path, data: dict) -> None:
    """原子写回 extension.json：保留全部字段与键顺序，规范化 indent=4。"""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=4) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None and _POSIX:
            os.chmod(temp, mode)
        os.replace(temp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise OSError(f"cannot write {path}: {exc.strerror or exc}") from exc
    except Exception:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


def _load_extension(folder: Path) -> list[str] | None:
    """按包加载单个扩展；register 失败时回滚并上抛，缺 register 返回 None。"""
    module_name = _module_name(folder.name)
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
