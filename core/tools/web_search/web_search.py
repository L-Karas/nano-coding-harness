import asyncio
import os

import httpx
from pydantic import Field

from core.log import get_logger
from core.tools.tool_base import BaseTool
from core.tools.web_search.utils import update_provider_state, format_search_result, PROVIDER_STATE

_LOGGER = get_logger(__name__)


class WebSearch(BaseTool):
    """Web search tool. Use this tool when you need real-time or external information."""
    query: str = Field(description="The web query to search for.")
    agent_type: set = {"main", "sub-agent", "teammate"}


async def firecrawl_search(query: str) -> tuple[bool, str]:
    url = "https://api.firecrawl.dev/v2/search"
    payload = {
        "query": query,
        "sources": ["web"],
        "limit": 10
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('FIRECRAWL_API_KEY')}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=url,
                headers=headers,
                json=payload
            )
        response_json = response.json()
        if not response_json["success"]:
            if response.status_code in (400,):
                return False, str(response.json()["error"])
            if response.status_code in (401, 402, 403):
                update_provider_state("firecrawl", enable=False)
            return False, str(response.json()["error"])
        else:
            formated_results = format_search_result(response_json["data"]["web"], provider="firecrawl")
            return True, formated_results
    except Exception as e:
        _LOGGER.exception(e)
        return False, str(e)


async def tavily_search(query: str) -> tuple[bool, str]:
    url = "https://api.tavily.com/search"
    payload = {
        "query": query,
        "search_depth": "advanced",
        "max_results": 10
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('TAVILY_API_KEY')}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=url,
                headers=headers,
                json=payload
            )
        response_json = response.json()
        if response.status_code != 200:
            if response.status_code in (401, 429, 433):
                update_provider_state("tavily", enable=False)
            return False, str(response_json)
        else:
            return True, format_search_result(response_json.get("results", []))
    except Exception as e:
        _LOGGER.exception(e)
        return False, str(e)


async def exa_search(query: str) -> tuple[bool, str]:
    url = "https://api.exa.ai/search"
    payload = {
        "query": query,
        "numResults": 10,
        "type": "auto",
        "contents": {
            "highlights": True
        }
    }
    headers = {
        "content-type": "application/json",
        "x-api-key": os.getenv("EXA_API_KEY")
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=url,
                headers=headers,
                json=payload
            )
        response_json = response.json()
        if response.status_code == 200:
            return True, format_search_result(response_json.get("results", []))
        else:
            if response.status_code in (401, 402):
                update_provider_state("exa", enable=False)
            return False, response_json.get("error", "")
    except Exception as e:
        _LOGGER.exception(e)
        return False, str(e)


async def ddgs_search(query: str) -> tuple[bool, str]:
    import ddgs

    try:
        with ddgs.DDGS() as client:
            # 不指定 backend：ddgs 默认 auto，按当前版本选择可用引擎
            response = await asyncio.to_thread(client.text, query=query)
        if not response:
            return False, "(No search results)"
        return True, format_search_result(response, "ddgs")
    except Exception as e:
        _LOGGER.exception(e)
        return False, str(e)


async def run_web_search_async(query: str, ctx=None) -> str:
    try:
        if PROVIDER_STATE.firecrawl_enabled:
            ok, result = await firecrawl_search(query)
            if ok:
                return result
        if PROVIDER_STATE.tavily_enabled:
            ok, result = await tavily_search(query)
            if ok:
                return result
        if PROVIDER_STATE.exa_enabled:
            ok, result = await exa_search(query)
            if ok:
                return result
        # todo: brave search
        if PROVIDER_STATE.brave_search_enabled:
            pass
        _, result = await ddgs_search(query)
        return result
    except Exception as e:
        return str(e)
