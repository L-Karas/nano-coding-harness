"""配置模块说明。

本模块负责环境设置和提供共享的配置对象，包括 API 客户端初始化、模型设置和工作目录路径。
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

WORKDIR = Path.cwd().parent
MEMORY_DIR = WORKDIR / ".memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
MEMORY_DIR.mkdir(exist_ok=True)

TASK_DIR = WORKDIR / ".task"
TASK_DIR.mkdir(exist_ok=True)

MAILBOX_DIR = WORKDIR / ".mailbox"
MAILBOX_DIR.mkdir(exist_ok=True)

client = OpenAI(api_key=os.getenv("DASHSCOPE_API_KEY"), base_url=os.getenv("DASHSCOPE_BASE_URL"))
MODEL = os.getenv("MODEL")
SUB_MODEL = os.getenv("SUB_MODEL")
