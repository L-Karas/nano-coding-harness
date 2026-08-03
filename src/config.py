import os
from pathlib import Path

import dotenv
from openai import OpenAI

dotenv.load_dotenv()

try:
    import readline

    readline.parse_and_bind('set bind-tty-special-chars off')
    READLINE_AVAILABLE = True
except ImportError:
    READLINE_AVAILABLE = False

client = OpenAI(
    api_key=os.getenv("BIGMODEL_API_KEY"),
    base_url=os.getenv("BIGMODEL_BASE_URL")
)
SUB_MODEL = os.getenv("SUB_MODEL")
PRIMARY_MODEL = os.getenv("MODEL")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL")

WORKDIR = Path.cwd()
SKILL_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool_results"

DEFAULT_MAX_TOKENS = int(8e3)
ESCALATED_MAX_TOKENS = int(16e3)
MAX_RETRIES = 3
MAX_RECOVERY_RETRIES = 2
MAX_CONSECUTIVE = 2

BASE_DELAY_MS = 500
CONTEXT_LIMIT = int(5e4)
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = int(3e4)

CONTINUATION_PROMPT = "Continue from the previous response. Do not repeat completed work."

PROMPT = "\033[36ms20 >> \033[0m"
CLI_ACTIVE = True
