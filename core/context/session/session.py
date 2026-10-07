"""
Session 模块

用于 session 保存，加载：
session_index.jsonl 每行一个会话元数据：
{"id": "...", "title": "...", "timestamp": "...", "messages": []}
每个 session 以 session-{...}.jsonl 保存，每行一条消息（Message 的 JSON）。
"""
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Literal, Any

from core.config import SESSION_DIR, SESSION_INDEX_FILE
from core.context.token import estimate_size
from core.log.log import get_logger

_LOGGER = get_logger(__name__)

MESSAGE_PREVIEW_CHARS = 30  # 用户消息截断展示长度（/fork 列表预览与 fork 会话标题 [Fork] 共用）


def _get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_session_id() -> str:
    return f"session-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}-{uuid.uuid4().hex}.json"


def _get_message_id() -> str:
    return f"message-{uuid.uuid4().hex}"


def _truncate(text: str) -> str:
    """按 MESSAGE_PREVIEW_CHARS 截断，超长加省略号。"""
    return text[:MESSAGE_PREVIEW_CHARS] + ("…" if len(text) > MESSAGE_PREVIEW_CHARS else "")


def _fork_title(content: str) -> str:
    """fork 新会话标题："[Fork] " + 选中消息内容（压平空白后截断）。"""
    return "[Fork] " + _truncate(" ".join(content.split()))


@dataclass
class Message:
    """
    Message 类，记录消息内容
    payload 属性用于记录附带信息，例如修改文件后的 git diff 信息，用于信息重新渲染时使用
    """
    role: Literal["user", "assistant", "tool"]
    id: str = field(default_factory=_get_message_id)
    content: str = ""
    reasoning_content: str = ""
    tool_call_id: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    payload: Any = ""
    timestamp: str = field(default_factory=_get_timestamp)


@dataclass
class Session:
    """
    Session 类，记录会话消息列表，会话标题，会话保存路径
    """
    id: str = field(default_factory=_get_session_id)
    messages: list[Message] = field(default_factory=list)
    title: str = ""
    timestamp: str = field(default_factory=_get_timestamp)


class SessionManager:
    """
    会话管理器，用于创建、更新、删除和保存会话
    """

    def __init__(self):
        # session file name -> session
        self.session_map: dict[str, Session] = {}
        # current session file name
        self.current_session: str = ""

    def load_session_tokens(self) -> int:
        """
        加载当前会话消息 token 总数
        Returns:

        """
        return estimate_size(self.load_messages()) if self.current_session else 0

    def _update_session_title(self, user_query: str):
        if not self.current_session:
            return
        if user_query.startswith(("<", "[")):
            return
        self.session_map[self.current_session].title = _truncate(user_query)

    def add_message(self, message: dict) -> bool:
        """
        新增并保存消息，若当前会话不存在，则创建新会话；
        """
        if not self.current_session:
            self.new_session()

        message = Message(**message)
        if message.role == "user":
            self._update_session_title(message.content)

        self.session_map[self.current_session].messages.append(message)
        self.update_session(update_type="append")
        return True

    def load_messages(self) -> list[dict]:
        """
        加载当前会话消息列表（含 id / usage / payload 全部字段），若当前会话不存在，则创建新会话。
        发给模型前须经 core.context.to_llm_messages() 裁成 API 视图。
        """
        if not self.current_session:
            self.new_session()

        return [asdict(message) for message in self.session_map[self.current_session].messages]

    def load_user_messages(self) -> list[Message] | None:
        """
        仅加载用户消息，为 fork 新会话提供节点
        Returns:
            当前会话的所有用户消息
        """
        if not self.current_session or not self.session_map[self.current_session].messages:
            return None

        messages = []
        for message in self.session_map[self.current_session].messages:
            if message.role == "user":
                messages.append(message)

        return messages

    def update_messages(self, messages_dict: list[dict]) -> bool:
        """回写消息列表：入参来自 load_messages()（压缩步骤只改 content，元数据全量带回）。"""
        if not messages_dict:
            return True

        self.session_map[self.current_session].messages = [Message(**message) for message in messages_dict]
        self.update_session(update_type="rewrite")
        return True

    def _update_session_index(self) -> bool:
        """
        重写会话索引文件；若删除当前会话后更新，则无需更新当前会话时间戳
        """
        if self.current_session:
            self.session_map[self.current_session].timestamp = _get_timestamp()

        lines = []
        for session in self.session_map.values():
            session_dict = asdict(session)
            session_dict["messages"] = []
            lines.append(json.dumps(session_dict, ensure_ascii=False))

        try:
            SESSION_INDEX_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except Exception as e:
            _LOGGER.exception(e)
            return False
        return True

    def _update_session_messages(self, update_type: Literal["rewrite", "append"] = "rewrite") -> bool:
        """
        更新会话文件
        """
        session_path = SESSION_DIR / self.current_session
        if not session_path.exists():
            session_path.touch()
        try:
            messages = self.session_map[self.current_session].messages
            if update_type == "rewrite":
                with open(session_path, "w", encoding="utf-8") as f:
                    content = "\n".join(
                        json.dumps(asdict(message), ensure_ascii=False)
                        for message in messages
                    )
                    f.write(content)
            else:
                with open(session_path, "a", encoding="utf-8") as f:
                    content = "\n" + json.dumps(asdict(messages[-1]), ensure_ascii=False)
                    f.write(content)
        except Exception as e:
            _LOGGER.exception(e)
            return False

        return True

    def delete_session(self, session_id: str) -> bool:
        """
        删除会话，若删除当前会话，则将当前会话置空
        """
        if self.current_session == session_id:
            self.current_session = ""

        session_path = SESSION_DIR / session_id
        session_path.unlink(missing_ok=True)
        self.session_map.pop(session_id)
        self._update_session_index()
        return True

    def update_session(self, update_type: Literal["rewrite", "append"] = "rewrite") -> bool:
        """
        更新会话文件和会话索引文件，若当前会话为空，则创建新会话

        update_type: 更新会话消息记录方式。rewrite 指重写整个消息记录进文件；append 指将新消息写入文件最后一行
        """
        if not self.current_session:
            self.new_session()

        self._update_session_messages(update_type=update_type)
        self._update_session_index()

        return True

    def new_session(self, messages: list[Message] | None = None, title: str = "") -> Session:
        """
        创建新会话：置为当前会话并入映射表。
        messages 非空（fork 前缀）时立即落盘并写会话索引（标题随索引落盘）；空会话
        （fork 到起点 / 新会话）不建文件、不写索引，等首条消息经 add_message /
        update_session 落盘时再补（同 /new 的延迟创建）。
        """

        try:
            new_session = Session(title=title)
            if messages:
                new_session.messages = messages[:]
            self.current_session = new_session.id
            self.load_session_list()
            self.session_map[self.current_session] = new_session
            if messages:  # 有历史才需要建档：内存与文件一致，否则重读即丢前缀
                path = SESSION_DIR / self.current_session
                path.touch()
                self._update_session_messages(update_type="rewrite")
                self._update_session_index()
        except Exception as e:
            _LOGGER.exception(f"[Session create] error: {e!r}")
            raise

        return self.session_map[self.current_session]

    def fork_session(self, message_id: str) -> Session | None:
        """
        从当前会话中信息id为 message_id 处分叉出新会话：新会话消息 = 该消息之前的前缀，
        标题 = "[Fork] " + 该消息内容（截断长度见 MESSAGE_PREVIEW_CHARS）。
        选中首条消息（前缀为空）时不建文件、不写索引，等首条消息落盘时再建。
        Args:
            message_id:

        Returns:
            新创建的 Session
        """
        if not self.current_session or not message_id:
            _LOGGER.info(f"[Session fork] error: current_session: {self.current_session}, message_id: {message_id}")
            return None

        for index, message in enumerate(self.session_map[self.current_session].messages):
            if message_id == message.id:
                _LOGGER.info(f"[Session fork] fork messages len: {index}")
                return self.new_session(self.session_map[self.current_session].messages[:index],
                                        title=_fork_title(message.content))
        _LOGGER.info(f"[Session fork] message not found: {message_id}")
        return None

    def load_session_list(self) -> list[Session]:
        """
        加载会话索引中的全部会话（仅元数据），更新会话映射表
        """

        try:
            with open(SESSION_INDEX_FILE, "r", encoding="utf-8") as f:
                content_lines = f.readlines()
            sessions = []
            for line in content_lines:
                session = Session(**json.loads(line.strip()))
                sessions.append(session)
                self.session_map[session.id] = session

            return sessions
        except Exception as e:
            _LOGGER.exception(e)

        return []

    def load_session(self, session_id: str = "") -> Session | None:
        """
        加载会话消息：提供会话 id 时加载对应会话，否则加载当前会话。
        始终从会话文件重读：load_session_list 只保留索引元数据，内存中的 messages
        可能已被重置，不能据此短路返回。
        """
        if session_id:
            # 切换会话：清空上一会话内存消息，待需要时重新从文件读取
            if self.current_session and self.current_session != session_id:
                self.session_map[self.current_session].messages = []
            self.current_session = session_id

        path = SESSION_DIR / self.current_session
        if not path.exists():
            _LOGGER.error(f"Session file not found: {path}")
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                content_lines = f.readlines()
            self.session_map[self.current_session].messages = [
                Message(**json.loads(line.strip()))
                for line in content_lines if line.strip()  # 跳过 append 写入的首行 / 记录间空行，否则整个会话读不回
            ]
            return self.session_map[self.current_session]
        except Exception as e:
            _LOGGER.exception(e)
            return None


SESSION_MANAGER = SessionManager()
