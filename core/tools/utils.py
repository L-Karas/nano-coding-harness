import locale
import re
from typing import Any

from pydantic import BaseModel


def _to_text(data: bytes) -> str:
    """解码工具输出：优先 UTF-8，失败时回退本机 locale 编码。

    Args:
        data: 原始字节。

    Returns:
        解码后的文本；全部编码失败时按 UTF-8 有损解码。
    """
    for enc in ("utf-8", locale.getpreferredencoding()):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", errors="replace")


def _inline_refs(schema: dict, defs: dict) -> dict:
    """递归展开 #/$defs/X 引用，使 schema 自包含。

    Args:
        schema: 待处理的 schema 片段。
        defs: 当前 schema 的 $defs 映射。

    Returns:
        展开引用后的 schema 片段。
    """
    if isinstance(schema, dict):
        if (ref := schema.get("$ref")) and ref.startswith("#/$defs/"):
            return _inline_refs(defs[ref.removeprefix("#/$defs/")], defs)
        return {k: _inline_refs(v, defs) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_inline_refs(v, defs) for v in schema]
    return schema


def _json_schema_to_openai_params(schema: dict) -> dict:
    """把 JSON Schema 收敛为 OpenAI function parameters。

    Args:
        schema: 完整 JSON Schema。

    Returns:
        仅含 type / properties / required 的参数 schema。
    """
    return {
        "type": "object",
        "properties": _inline_refs(schema.get("properties", {}), schema.get("$defs", {})),
        "required": schema.get("required", []),
    }


def _camel_to_snake(name: str) -> str:
    """把 CamelCase 名称转换为 snake_case。

    Args:
        name: 原始名称。

    Returns:
        snake_case 名称。
    """
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def to_openai_tool(model: type[BaseModel]) -> dict[str, Any]:
    """把 pydantic 模型转换为 OpenAI tool schema。

    Args:
        model: 工具模型类。

    Returns:
        含 name / description / parameters 的 function schema。
    """
    tool_schema = model.model_json_schema(by_alias=True)

    params = _json_schema_to_openai_params(tool_schema)
    # remove internal field from schema
    params["properties"].pop("agent_type")
    params["properties"].pop("experimental")
    params["properties"].pop("parallel_safe")
    params["required"] = [r for r in params.get("required", []) if r != "agent_type"]

    return {
        "type": "function",
        "function": {
            "name": _camel_to_snake(model.__name__),
            "description": model.__doc__ or "",
            "parameters": params,
        },
    }
