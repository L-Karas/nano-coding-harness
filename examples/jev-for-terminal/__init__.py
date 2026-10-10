import os


def register(api):
    if not os.getenv("TYPESAFE_API_KEY"):
        raise ValueError("缺少 Typesafe AI API KEY，无法使用该扩展")

    api.on("before_tool", on_before_terminal)


async def on_before_terminal(ctx):
    pass
