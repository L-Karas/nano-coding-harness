"""扩展加载器：读取 .harness/extensions 下的扩展包，统一注册并按清单开关派发。

每个扩展是一个含 extension.json 与 __init__.py 的目录；register(api) 通过
ExtensionAPI 缓冲注册，正常返回后由 host 一次性提交到派发器，失败则整体作废（原子性）。
清单合法即加载注册；extension.json.enabled 只控制是否派发，便于 /extensions 即时启停。
"""
import inspect
import json
import sys
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Iterable, NamedTuple

import core.extension.dispatcher as dispatcher
import core.extension.registry as registry
from core.config import EXTENSION_DIR
from core.extension.api import ExtensionAPI
from core.extension.host import commit_extension
from core.shared.file_io import atomic_write_text
from core.shared.logger import get_logger

_LOGGER = get_logger(__name__)

_LOADED = False
_LOADING = False
_FOLDERS: dict[str, Path] = {}
_LOAD_ERRORS: dict[str, str] = {}


class _ManifestState(NamedTuple):
    """清单扫描结果：reason 为 None 表示清单合法；data 为解析后的完整清单。"""

    reason: str | None
    description: str
    enabled: bool
    data: dict


@dataclass(frozen=True)
class ToggleResult:
    """一次启停操作的结果；状态与展示信息分开，调用方不需要再推导。"""

    ok: bool
    message: str
    warning: bool = False


def load_extensions(directory: Path | None = None) -> None:
    """加载全部扩展；进程内幂等，重复调用直接返回。

    清单合法的扩展无论 enabled 都会 import + register（保持注册顺序稳定），
    enabled 决定模块是否参与派发；清单缺失 / 非法仍按未加载处理。
    目录级异常时允许调用方重试：已处理过的文件夹会跳过，避免重复注册。
    """
    global _LOADED, _LOADING
    if _LOADED or _LOADING:
        return
    _LOADING = True
    try:
        target = directory or EXTENSION_DIR
        if not target.exists():
            _LOADED = True
            return

        def _record(
            folder: Path,
            description: str,
            *,
            enabled: bool,
            detail: str = "",
            events: Iterable[str] = (),
            error: bool = False,
        ) -> None:
            """登记本目录的扫描结果；外部扩展来源固定为 user。"""
            registry.record(folder.name, "user", enabled=enabled, detail=detail,
                            events=events, description=description, error=error)

        # todo: 回调顺序与扩展文件夹名相关，应由用户控制
        for folder in sorted(target.iterdir()):
            if not folder.is_dir() or folder.name.startswith("_"):
                continue
            if folder.name in _FOLDERS:
                continue
            manifest = _manifest_state(folder)
            _FOLDERS[folder.name] = folder
            if manifest.reason is not None:
                _LOAD_ERRORS[folder.name] = manifest.reason
                _record(folder, manifest.description, enabled=False,
                        detail=manifest.reason, error=True)
                continue
            try:
                events = _load_extension(folder)
            except Exception:
                _LOGGER.exception(f"[Extension] 加载失败，已隔离: {folder.name}")
                _LOAD_ERRORS[folder.name] = "load failed"
                _record(folder, manifest.description, enabled=False,
                        detail=_failure_detail(manifest.enabled, "load failed"), error=True)
                continue
            if events is None:
                _LOAD_ERRORS[folder.name] = "missing register(api)"
                _record(folder, manifest.description, enabled=False,
                        detail=_failure_detail(manifest.enabled, "missing register(api)"),
                        error=True)
                continue
            dispatcher._set_module_enabled(_module_name(folder.name), manifest.enabled)
            _record(folder, manifest.description, enabled=manifest.enabled, events=events)
        _LOADED = True
    except Exception:
        _LOADED = False
        raise
    finally:
        _LOADING = False


def reset() -> None:
    """清空加载状态，供测试隔离使用；同时移除已加载的扩展模块。"""
    global _LOADED, _LOADING
    _LOADED = False
    _LOADING = False
    for name in [name for name in sys.modules if name.startswith("nano_extension_")]:
        sys.modules.pop(name, None)
    _FOLDERS.clear()
    _LOAD_ERRORS.clear()


def toggle_extension(name: str) -> ToggleResult:
    """翻转一个扩展的 enabled，并即时切换派发开关。"""
    folder = _FOLDERS.get(name)
    if folder is None:
        return ToggleResult(
            False,
            f"Extension '{name}' was not loaded in this session; restart to load it",
        )
    state = _manifest_state(folder)
    if state.reason is not None:
        return ToggleResult(False, f"Cannot toggle '{name}': {state.reason}")
    return _apply_enabled(name, folder, state, not state.enabled)


def _apply_enabled(
        name: str,
        folder: Path,
        state: _ManifestState,
        enabled: bool,
) -> ToggleResult:
    """写入 enabled 并在写盘成功后切换派发；失败时不改变运行态。"""
    entry = _entry(name)
    if entry is None:
        return ToggleResult(
            False,
            f"Extension '{name}' was not loaded in this session; restart to load it",
        )
    data = {**state.data, "enabled": bool(enabled)}
    try:
        _write_manifest(folder / "extension.json", data)
    except Exception as exc:  # 文件系统 / 编码错误都回滚，不能让 UI 动作抛穿
        _LOGGER.error(f"[Extension] 写入清单失败 {name}: {exc!r}")
        return ToggleResult(False, f"Failed to save extension state: {exc}")

    events = entry.events
    description = entry.description

    if name not in _LOAD_ERRORS:
        dispatcher._set_module_enabled(_module_name(name), bool(enabled))
        registry.record(name, "user", enabled=bool(enabled), detail="",
                        events=events, description=description)
        action = "Enabled" if enabled else "Disabled"
        return ToggleResult(True, f"{action} extension '{name}'")

    error = _LOAD_ERRORS.get(name, "load failed")
    registry.record(name, "user", enabled=False,
                    detail=error if enabled else f"disabled · {error}",
                    events=events, description=description, error=True)
    if enabled:
        return ToggleResult(
            True,
            f"Set extension '{name}' to enabled, but it still fails to load: {error}",
            warning=True,
        )
    return ToggleResult(True, f"Disabled extension '{name}'")


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
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=4) + "\n")


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
        if inspect.iscoroutinefunction(register):
            raise TypeError("register(api) 必须是同步函数")

        api = ExtensionAPI()
        result = register(api)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("register(api) 必须是同步函数")
        registered = commit_extension(api, module_name)
        _LOGGER.info(f"[Extension] 加载成功: {folder.name}（注册 {len(registered)} 个回调）")
        return [registration.event for registration in registered]
    except Exception:
        _purge_modules(module_name)
        raise


def _purge_modules(module_name: str) -> None:
    """移除扩展主模块及其子模块，避免失败 / reset 后残留旧代码。"""
    for name in [name for name in sys.modules
                 if name == module_name or name.startswith(f"{module_name}.")]:
        sys.modules.pop(name, None)
