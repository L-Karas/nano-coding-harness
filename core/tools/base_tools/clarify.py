from pydantic import Field

from core.interaction import ask_clarify
from core.runtime_context import ToolContext
from core.tools.tool_base import BaseTool

MAX_OPTIONS = 4
DESCRIPTION = (
    f"Ask the user to choose an answer when you need a decision, clarification, or feedback before proceeding. "
    f"`options` holds 1-{MAX_OPTIONS} candidate answers; the user selects one (or several with `multi_select=true`) "
    f"or types a custom answer instead, and the selection is returned as the tool result. "
    f"The entries are rendered as a bare list with no separate question line, so each one must be a complete, "
    f"self-contained reply that makes sense as the answer on its own, not a description of the problem. "
    f"Make entries mutually exclusive and order them by preference, best first. "
    f"Pass `options=[]` only for a fully open-ended question with no meaningful preset answers."
)


class Clarify(BaseTool):
    __doc__ = DESCRIPTION

    options: list[str] = Field(
        description=f"Candidate answers presented to the user as selectable entries (1-{MAX_OPTIONS}; a single "
                    f"choice is a one-entry list). A chosen entry is returned verbatim as the answer, so each entry "
                    f"must already read as a complete reply on its own. Entries must be mutually exclusive and "
                    f"ordered by preference, best first. Pass [] for a fully open-ended question.")
    multi_select: bool = Field(
        default=False,
        description="Whether the user may select multiple entries from `options` instead of one "
                    "(default: false, single selection; multiple selections are returned as a bulleted list).")

    agent_type: set = {"main", "sub-agent"}

    def run(self, tctx: ToolContext | None = None) -> str:
        """经交互端口弹出澄清列表并阻塞等待作答；无 UI / 取消时返回 "[User cancelled]"。"""
        return ask_clarify(self.options, self.multi_select) or "[User cancelled]"
