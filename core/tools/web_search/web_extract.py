import asyncio
import os
import shutil

import httpx
from pydantic import Field

from core.tools.tool_base import BaseTool
from core.tools.utils import _to_text
from core.tools.web_search.utils import update_provider_state, NO_CONTENT, PROVIDER_STATE


class WebExtract(BaseTool):
    """A web extraction tool used to extract content from web URLs. Call this tool when you need to extract content from a specific URL."""
    url: str = Field(
        description="The URL of the web page to extract content from.")
    agent_type: set = {"main", "sub-agent", "teammate"}


async def defuddle_extract(url: str) -> tuple[bool, str]:
    try:
        npx = shutil.which("npx")
        if not npx:
            raise RuntimeError("[Web Extraction] npx not found; use another method to extract the URL content")
        # 参数分离（非 shell 拼接）：URL 里的 shell 元字符不再有注入/截断风险
        process = await asyncio.create_subprocess_exec(
            npx, "defuddle", "parse", url, "--md",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(_to_text(stderr))

        content = _to_text(stdout)
        return True, content
    except RuntimeError as e:
        return False, str(e)
    except Exception as e:
        raise


async def firecrawl_extract(url: str) -> tuple[bool, str]:
    api_url = "https://api.firecrawl.dev/v2/scrape"
    payload = {
        "url": url,
        "onlyMainContent": True,
        "maxAge": 172_800_000,
        "parsers": [
            "pdf"
        ],
        "formats": [
            "markdown"
        ]
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('FIRECRAWL_API_KEY')}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=api_url,
                headers=headers,
                json=payload
            )
        response_json = response.json()
        if response.status_code == 200:
            if response_json.get("success"):
                return True, response_json.get("data").get("markdown", NO_CONTENT)
            else:
                return True, response_json.get("error", NO_CONTENT)
        elif response.status_code in (400, 401, 402, 403):
            update_provider_state("firecrawl", enable=False)
        return False, response_json.get("error", NO_CONTENT) + response_json.get("details", "")
    except Exception as e:
        return False, str(e)


async def tavily_extrack(url: str) -> tuple[bool, str]:
    api_url = "https://api.tavily.com/extract"
    payload = {
        "urls": [url],
    }
    headers = {
        "Authorization": f"Bearer {os.getenv('TAVILY_API_KEY')}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=api_url,
                headers=headers,
                json=payload
            )
        response_json = response.json()
        if response.status_code == 200:
            if response_json.get("results", []):
                return True, response_json.get("results")[0].get("raw_content", NO_CONTENT)
            else:
                error = response_json.get("failed_results")[0].get("error", NO_CONTENT)
                if "Error fetching content" in error:
                    return False, error
                return True, error
        elif response.status_code in (401, 432, 433):
            update_provider_state("tavily", enable=False)
        return False, response_json.get("detail", {}).get("error", NO_CONTENT)
    except Exception as e:
        return False, str(e)


async def exa_extract(url: str) -> tuple[bool, str]:
    api_url = "https://api.exa.ai/contents"
    headers = {
        "content-type": "application/json",
        "x-api-key": os.getenv("EXA_API_KEY")
    }
    payload = {
        "ids": [url],
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=api_url,
                headers=headers,
                json=payload
            )
        response_json = response.json()
        if response.status_code == 200:
            status = response_json.get("statuses", {})
            if status.get("status") == "success":
                return True, response_json.get("results")[0].get("text", NO_CONTENT)
            else:
                return True, str(status)
        else:
            if response.status_code in (400, 401, 402):
                update_provider_state("exa", enable=False)
            return False, response_json.get("error", NO_CONTENT)
    except Exception as e:
        return False, str(e)


async def ddgs_extract(url: str) -> tuple[bool, str]:
    import ddgs
    try:
        with ddgs.DDGS() as client:
            response = await asyncio.to_thread(client.extract, url)
        return True, response.get("content", NO_CONTENT)
    except Exception:
        raise


async def run_web_extract_async(url: str, ctx=None) -> str:
    if not url:
        return NO_CONTENT

    try:
        # todo: tool logger
        ok, content = await defuddle_extract(url)
        if ok:
            return content
        if PROVIDER_STATE.firecrawl_enabled:
            ok, content = await firecrawl_extract(url)
            if ok:
                return content
        if PROVIDER_STATE.tavily_enabled:
            ok, content = await tavily_extrack(url)
            if ok:
                return content
        if PROVIDER_STATE.exa_enabled:
            ok, content = await exa_extract(url)
            if ok:
                return content
        ok, content = await ddgs_extract(url)
        if ok:
            return content
    except Exception as e:
        content = str(e)

    # todo: tool output budget
    return (content or NO_CONTENT)[:15000]
