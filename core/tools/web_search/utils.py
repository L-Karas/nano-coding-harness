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


def format_search_result(
        search_results: list[dict],
        provider: Literal["firecrawl", "tavily", "exa", "brave_search", "ddgs"]
) -> str:
    if not search_results:
        return "(no search result)"

    results = []
    result_format = "**TITLE**: {title}\n**URL**: {url}\n**DESCRIPTION**: {description}"
    default_value = "(none)"
    if provider == "firecrawl":
        for result in search_results:
            results.append(
                result_format.format(
                    title=result.get("title", default_value),
                    url=result.get("url", default_value),
                    description=result.get("description", default_value)
                )
            )
    elif provider == "tavily":
        for result in search_results:
            results.append(
                result_format.format(
                    title=result.get("title", default_value),
                    url=result.get("url", default_value),
                    description=result.get("content", default_value)
                )
            )
    elif provider == "exa":
        for result in search_results:
            results.append(
                result_format.format(
                    title=result.get("title", default_value),
                    url=result.get("url", default_value),
                    description="\n".join(result.get("highlights", [default_value]))
                ) + f"\n**PUBLISHED DATE**: {result.get('publishedDate', default_value)}"
            )
    # todo: brave search
    elif provider == "brave_search":
        pass
    else:
        for result in search_results:
            results.append(
                result_format.format(
                    title=result.get("title", default_value),
                    url=result.get("href", default_value),
                    description=result.get("body", default_value)
                )
            )

    return "\n---\n".join(results)


def update_provider_state(provider: Literal["firecrawl", "tavily", "exa", "brave_search"],
                          enable: bool) -> None:
    if provider == "firecrawl":
        PROVIDER_STATE.firecrawl_enabled = enable
    elif provider == "tavily":
        PROVIDER_STATE.tavily_enabled = enable
    elif provider == "exa":
        PROVIDER_STATE.exa_enabled = enable
    elif provider == "brave_search":
        PROVIDER_STATE.brave_search_enabled = enable
