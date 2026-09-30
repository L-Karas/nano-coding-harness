import locale
from typing import Any

from pydantic import BaseModel


def _to_text(data: bytes) -> str:
    """Decode tool output: git and most unix tools emit UTF-8, native Windows tools the locale codec."""
    for enc in ("utf-8", locale.getpreferredencoding()):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", errors="replace")


def _inline_refs(schema: dict, defs: dict) -> dict:
    """Replace #/$defs/X references recursively so the schema is self-contained."""
    if isinstance(schema, dict):
        if (ref := schema.get("$ref")) and ref.startswith("#/$defs/"):
            return _inline_refs(defs[ref.removeprefix("#/$defs/")], defs)
        return {k: _inline_refs(v, defs) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_inline_refs(v, defs) for v in schema]
    return schema


def _json_schema_to_openai_params(schema: dict) -> dict:
    return {
        "type": "object",
        "properties": _inline_refs(schema.get("properties", {}), schema.get("$defs", {})),
        "required": schema.get("required", []),
    }


def _camel_to_snake(name: str) -> str:
    """
    Converts a camel-case string to snake-case
    """
    import re
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def to_openai_tool(model: type[BaseModel]) -> dict[str, Any]:
    """
    Convert pydantic model to openai tool schema.
    """
    tool_schema = model.model_json_schema(by_alias=True)

    params = _json_schema_to_openai_params(tool_schema)
    # remove internal field from schema
    params["properties"].pop("agent_type")
    params["properties"].pop("experimental")
    params["required"] = [r for r in params.get("required", []) if r != "agent_type"]

    return {
        "type": "function",
        "function": {
            "name": _camel_to_snake(model.__name__),
            "description": model.__doc__ or "",
            "parameters": params,
        },
    }
