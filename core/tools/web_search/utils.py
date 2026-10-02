import os
from dataclasses import dataclass
from typing import Literal


@dataclass
class WebSearchProviderState:
    firecrawl_enabled: bool = True
    tavily_enabled: bool = True
    exa_enabled: bool = True
    brave_search_enabled: bool = True


PROVIDER_STATE: WebSearchProviderState = WebSearchProviderState(
    firecrawl_enabled=bool(os.getenv("FIRECRAWL_API_KEY")),
    tavily_enabled=bool(os.getenv("TAVILY_API_KEY")),
    exa_enabled=bool(os.getenv("EXA_API_KEY")),
    brave_search_enabled=bool(os.getenv("BRAVE_SEARCH_API_KEY"))
)
PROVIDER_LIST = ["firecrawl", "tavily", "exa", "brave_search", "ddgs"]
NO_CONTENT = "(No content)"

_RESULT_FORMAT = "**TITLE**: {title}\n**URL**: {url}\n**DESCRIPTION**: {description}"
_NO_VALUE = "(none)"
# provider -> 结果里承载 url 的字段；ddgs 用 href，其余用 url
_URL_FIELD = {"firecrawl": "url", "tavily": "url", "exa": "url", "ddgs": "href"}
_DESC_FIELD = {"firecrawl": "description", "tavily": "content", "ddgs": "body"}


def format_search_result(
        search_results: list[dict],
        provider: Literal["firecrawl", "tavily", "exa", "brave_search", "ddgs"]
) -> str:
    if not search_results:
        return "(no search result)"
    if provider == "brave_search":  # 未实现：保持旧行为返回空串
        return ""

    results = []
    for result in search_results:
        if provider == "exa":
            description = "\n".join(result.get("highlights", [_NO_VALUE]))
        else:
            description = result.get(_DESC_FIELD[provider], _NO_VALUE)
        formatted = _RESULT_FORMAT.format(
            title=result.get("title", _NO_VALUE),
            url=result.get(_URL_FIELD[provider], _NO_VALUE),
            description=description,
        )
        if provider == "exa":
            formatted += f"\n**PUBLISHED DATE**: {result.get('publishedDate', _NO_VALUE)}"
        results.append(formatted)

    return "\n---\n".join(results)


def update_provider_state(provider: Literal["firecrawl", "tavily", "exa", "brave_search"],
                          enable: bool) -> None:
    setattr(PROVIDER_STATE, f"{provider}_enabled", enable)
