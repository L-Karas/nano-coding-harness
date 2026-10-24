from core.runtime_state import RUNTIME_STATE, TodoEntry
from core.shared.logger import get_logger
from core.template import TODO_ITEM_MESSAGE_TEMPLATE, TODOS_MESSAGE_TEMPLATE

_LOGGER = get_logger(__name__)


def todo_write(todos: list[TodoEntry]) -> str:
    """整体替换当前 todo 清单并回显；参数校验由 ToolPool（TodoWrite 模型）负责。"""
    todo_messages = [
        TODO_ITEM_MESSAGE_TEMPLATE.format(content=todo.content, status=todo.status)
        for todo in todos
    ]
    RUNTIME_STATE.set_todos(list(todos))
    _LOGGER.info(f"[Todo Update] updated {len(todos)} item(s)")

    return f"Updated todos, current todos:\n {TODOS_MESSAGE_TEMPLATE.format(todos='\n'.join(todo_messages))}"
