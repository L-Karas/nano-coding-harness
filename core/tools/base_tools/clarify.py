from pydantic import Field

from core.interaction import ask_clarify
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool

MAX_OPTIONS = 4
DESCRIPTION = (
    f"Ask the user to choose an answer when you need a decision or clarification before proceeding. "
    f"`options` holds 1-{MAX_OPTIONS} candidate answers; the user picks one, picks several with "
    f"`multi_select=true`, or types a custom reply (always available, so no option needs to prompt for it). "
    f"Options are shown as a bare list with no question line, so each must read as the answer itself; "
    f"make them mutually exclusive and order them best first. "
    f"Pass `options=[]` only for a fully open-ended question."
)


class Clarify(BaseTool):
    __doc__ = DESCRIPTION

    options: list[str] = Field(
        description=f"Candidate answers shown to the user (1-{MAX_OPTIONS}). "
                    "A chosen entry is returned verbatim, so write each as a complete answer; "
                    "mutually exclusive, ordered best first (a custom reply is always available, so no entry "
                    "needs to offer one). Pass [] for an open-ended question.")
    multi_select: bool = Field(
        default=False,
        description="Allow selecting several options instead of one (default: false; "
                    "multiple selections return as a bulleted list).")

    agent_type: set = {"main", "sub-agent"}

    def run(self, tctx: ToolContext | None = None) -> str:
        """经交互端口弹出澄清列表并阻塞等待作答。

        Args:
            tctx: 运行时上下文；本工具未使用。

        Returns:
            用户选择的答案；无 UI 或取消时返回 "[User cancelled]"。
        """
        return ask_clarify(self.options, self.multi_select) or "[User cancelled]"
