from typing import Any

from pydantic import BaseModel


def _json_schema_to_openai_params(schema: dict) -> dict:
    return {
        "type": "object",
        "properties": schema.get("properties", {}),
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
    params["properties"].pop("agent_level", None)
    params["required"] = [r for r in params.get("required", []) if r != "agent_level"]

    return {
        "type": "function",
        "function": {
            "name": _camel_to_snake(model.__name__),
            "description": model.__doc__ or "",
            "parameters": params,
        },
    }
