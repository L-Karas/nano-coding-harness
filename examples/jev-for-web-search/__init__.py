import asyncio
import os
from contextvars import ContextVar

import httpx

from core.log import get_logger

MAX_RESULTS = 30
# 本次调用用户请求的结果数：before_tool 写入，after_tool 读取后据此截断。
# 用 ContextVar 而非模块全局，避免并发工具调用互相覆盖或跨调用残留。
_REQUESTED_MAX_RESULTS: ContextVar[int] = ContextVar("jev_requested_max_results", default=10)

_LOGGER = get_logger("jev-for-jugg")


def register(api):
    if not os.getenv("TYPESAFE_API_KEY"):
        raise ValueError("缺少 Typesafe AI API KEY，无法使用该扩展")

    api.on("before_tool", on_before_web_search)
    api.on("after_tool", on_after_web_search)


def build_request_body(query: str, search_result: str):
    return {
        "state": search_result,
        "model": "jev-latest",
        "questions": {
            "is_relevant": {
                "type": "noul",
                "instructions": f"The search result content is relevant to {query}?"
            }
        }
    }


def filter_results(jev_results: list[dict | Exception], search_results: list[str], max_results: int):
    """按 jev 相关性分数排序，返回前 max_results 个搜索结果（jev 失败项按 0.5 分兜底）。"""
    result_tuples = []
    for jev_result, search_result in zip(jev_results, search_results):
        if isinstance(jev_result, Exception):
            result_tuples.append((0.5, search_result))
            continue

        is_relevant = jev_result.get("answers", {}).get("is_relevant", {})
        result_tuples.append(
            (is_relevant.get("noul", 0.5), search_result)
        )

    _LOGGER.debug(f"result tuples: {result_tuples[:3]}")

    return [result for _, result in sorted(result_tuples, reverse=True)[:max_results]]


async def request_jev_jugg(request_body: dict):
    url = "https://api.typesafe.ai/v1/systemone"
    headers = {
        "Authorization": f"Bearer {os.getenv('TYPESAFE_API_KEY')}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=url,
            headers=headers,
            json=request_body
        )
    return response.json()


async def on_before_web_search(ctx):
    if ctx.tool_name != "web_search":
        return

    _REQUESTED_MAX_RESULTS.set(ctx.args.get("max_results", 10))
    ctx.args["max_results"] = MAX_RESULTS


async def on_after_web_search(ctx):
    if ctx.tool_name != "web_search":
        return
    if not ctx.args.get("query"):
        return
    if "\n---\n" not in ctx.result:
        return

    _LOGGER.debug(f"search results preview: {ctx.result[:100]}")
    _LOGGER.debug(f"split results len: {len(ctx.result.split('\n---\n'))}")

    search_results = [result.strip()
                      for result in ctx.result.split("\n---\n")
                      if result.strip()]

    _LOGGER.debug(f"search results len: {len(search_results)}")

    tasks = []
    for search_result in search_results:
        request_body = build_request_body(ctx.args["query"], search_result)
        tasks.append(request_jev_jugg(request_body))

    jev_results = await asyncio.gather(*tasks, return_exceptions=True)

    filtered_results = filter_results(jev_results, search_results, _REQUESTED_MAX_RESULTS.get())
    _LOGGER.debug(f"filtered results len: {len(filtered_results)}")

    if not filtered_results:
        _LOGGER.debug("return without filter")
        return
    else:
        ctx.result = "\n---\n".join(["## Jev filtered results"] + filtered_results)