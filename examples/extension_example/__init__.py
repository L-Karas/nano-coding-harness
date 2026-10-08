"""最小扩展示例：复制本目录到 .harness/extensions/ 后重启进程生效。"""


def register(api):
    api.on("before_llm", _mark_before_llm)
    api.on("after_tool", _mark_after_tool)


def _mark_before_llm(ctx):
    ctx.messages.append({"role": "user", "content": "[extension_example] before_llm 已触发"})


async def _mark_after_tool(ctx):
    ctx.result = f"{ctx.result}\n[extension_example] after_tool 已触发"
