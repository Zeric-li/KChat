import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Literal, Union
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment

from ..logger import kchat_logger as logger

"""
会话数据结构可视化:

1. 消息内容结构 (SimpleMessageContent)
   - type: "text" | "image_url"
   - data: 
     - text: str (当type="text")
     - image_url: {url: str, detail: str} (当type="image_url")

2. 单条消息结构 (SimpleMessage)
   - user_name: str
   - user_id: int
   - time: int (timestamp)
   - message: List[SimpleMessageContent]

3. 会话结构 (Session)
   - session_id: int
   - session_type: "private" | "group"
   - max_histories: int (默认10)
   - messages: List[SimpleMessage]
   - 文件存储: sessions/{session_type}_{session_id}.json

4. 会话管理器 (SessionManager)
   - sessions: Dict[int, Session]
   - 文件存储目录: sessions/

数据关系图:
SessionManager ────┬─── Session 1 ────┬─── Message 1 ────┬─── TextContent
                   │                  │                  └─── ImageContent
                   │                  └─── Message 2 ────┬─── TextContent
                   │                                     └─── ...
                   ├─── Session 2 ────┬─── Message 1
                   │                  └─── ...
                   └─── ...
"""

class SimpleMessageContent:
    def __init__(self, msg_type: Literal["text", "image_url"], data: str | Dict[str, str]) -> None:
        if msg_type == "image_url":
            if not isinstance(data, dict) or not all(key in data for key in ("url", "detail")):
                raise ValueError("msg_type为'image_url'时，data必须为包含'url'和'detail'的字典")
        else:
            if not isinstance(data, str):
                raise ValueError("msg_type为'text'时，data必须是字符串")
        
        self.type = msg_type  # 消息类型，支持text/image_url
        self.data = data  # 消息数据，文本内容为str，图片URL为dict{url, detail}

    def __repr__(self) -> str:
        return f"SimpleMessageContent(type={self.type}, data={self.data})"

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {"type": self.type, self.type: self.data}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SimpleMessageContent':
        """从字典反序列化"""
        if data["type"] not in ("text", "image_url"):
            raise ValueError("msg_type必须是'text'或'image_url'")
        return cls(data["type"], data[data["type"]])


class SimpleMessage:
    def __init__(self, message_event: MessageEvent, time: int=int(datetime.now().timestamp())) -> None:
        self.user_name: str = message_event.sender.nickname or ""  # 用户昵称
        self.user_id: int = message_event.user_id  # 用户ID
        self.time: int = time  # 消息时间，int类型的时间戳
        self.message: List[SimpleMessageContent] = []  # 消息段数据

        self._from_message(message_event.get_message())

    def _from_message(self, message_segments: list[MessageSegment]) -> None:
        """从MessageSegment列表中提取消息内容"""
        contents = []
        last_segment = None

        for segment in message_segments:
            # 忽略mface后的重复文本消息
            if segment.type == "text" and last_segment == "mface":
                # 本条文本消息与上一条mface表情消息的detail相同，则忽略
                if len(contents) > 0 and contents[-1].type == "image_url":
                    if contents[-1].data["detail"] == segment.data["text"]:
                        last_segment = "text"
                        continue

            # 文本消息
            if segment.type == "text":
                content = SimpleMessageContent("text", segment.data["text"])
                contents.append(content)
            # 图片消息
            elif segment.type == "image":
                content = SimpleMessageContent(
                    "image_url",
                    {"url": segment.data["url"], "detail": segment.data.get("summary", "low")}
                )
                contents.append(content)
            # mface表情消息
            elif segment.type == "mface":
                content = SimpleMessageContent(
                    "image_url", 
                    {"url": segment.data["url"], "detail": segment.data.get("summary", "low")}
                )
                contents.append(content)

            last_segment = segment.type

        self.message = contents

    def from_message_event(self, event: MessageEvent) -> None:
        """从MessageEvent中提取消息内容"""
        self.user_name = event.sender.nickname or ""  # 用户昵称
        self.user_id = event.user_id  # 用户ID
        self.time = event.time  # 消息时间
        self._from_message(event.get_message())  # 提取消息内容
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "user_name": self.user_name,
            "user_id": self.user_id,
            "time": datetime.fromtimestamp(self.time).strftime("%Y-%m-%d %H:%M:%S"),
            "message": [content.to_dict() for content in self.message]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SimpleMessage':
        """从字典反序列化"""
        msg = cls.__new__(cls)
        msg.user_name = data["user_name"]
        msg.user_id = data["user_id"]
        if isinstance(data["time"], str):
            msg.time = int(datetime.strptime(data["time"], "%Y-%m-%d %H:%M:%S").timestamp())
        elif isinstance(data["time"], int):
            msg.time = data["time"]
        else:
            raise ValueError("time字段必须是字符串或整数")
        msg.message = [
            SimpleMessageContent.from_dict(content) 
            for content in data["message"]
        ]
        return msg


class Session:
    def __init__(self, session_id: int, session_type: Literal["private", "group"], self_id: int, max_histories: int=10) -> None:
        if not isinstance(session_id, int) or session_id <= 0:
            raise ValueError("session_id必须是正整数")
        
        self.session_id = session_id
        self.session_type = session_type
        self.self_id = self_id
        self.max_histories = max_histories
        self.messages: List[SimpleMessage] = []

        self._session_dir = Path(os.path.dirname(os.path.abspath(__file__))) / "sessions"
        self._session_dir.mkdir(exist_ok=True)
        self._session_file = self._session_dir / f"{session_type}_{session_id}.json"
        self._load_session()

    def _load_session(self) -> None:
        """从文件加载会话数据"""
        if self._session_file.exists():
            try:
                with open(self._session_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.messages = [
                        SimpleMessage.from_dict(msg) 
                        for msg in data.get("messages", [])
                    ]
                    logger.debug(f"成功加载会话 {self.session_type}_{self.session_id} 的历史记录")
            except Exception as e:
                logger.opt(exception=e).error(f"加载会话文件 {self._session_file} 失败")

    def _save_session(self) -> None:
        """保存会话数据到文件"""
        try:
            with open(self._session_file, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            logger.debug(f"会话 {self.session_type}_{self.session_id} 已保存")
        except Exception as e:
            logger.opt(exception=e).error(f"保存会话文件 {self._session_file} 失败")

    def _try_merge_message(self, new_message: SimpleMessage) -> bool:
        """尝试合并消息到上一条"""
        if not self.messages:
            logger.debug(f"会话 {self.session_type}_{self.session_id} 没有消息，无法合并")
            return False
            
        last_message = self.messages[-1]
        
        # 检查是否满足合并条件
        if (last_message.user_id == new_message.user_id and 
            abs(last_message.time - new_message.time) <= 180):  # 3分钟=180秒
            # 合并消息内容
            last_message.message.extend(new_message.message)
            return True
            
        return False

    def add_message(self, event_or_message: Union[MessageEvent, SimpleMessage]) -> None:
        """添加消息到会话历史记录，支持消息合并"""
        message = event_or_message if isinstance(event_or_message, SimpleMessage) else SimpleMessage(event_or_message, event_or_message.time)

        # 检查是否可以合并到上一条消息
        if self._try_merge_message(message):
            logger.debug(f"合并消息到会话 {self.session_type}_{self.session_id}")
            self._save_session()
            return

        # 移除最早的消息
        if len(self.messages) >= self.max_histories:
            removed = self.messages.pop(0)
            logger.trace(f"移除最旧消息: {removed}")
        
        # 存储SimpleMessage对象
        self.messages.append(message)
        logger.debug(f"添加新消息到会话 {self.session_type}_{self.session_id}")
        self._save_session()

    def clear_history(self) -> None:
        """清空会话历史记录"""
        self.messages.clear()
        self._save_session()  # 保存清空后的状态到文件
        logger.debug(f"已清空会话 {self.session_type}_{self.session_id} 的历史记录")

    def to_dict(self) -> dict:
        """将会话转换为字典格式"""
        return {
            "session_id": self.session_id,
            "session_type": self.session_type,
            "self_id": self.self_id,
            "max_histories": self.max_histories,
            "messages": [msg.to_dict() for msg in self.messages]
        }

    def get_formatted_time(self) -> str:
        """获取格式化的当前时间"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def from_dict(cls, data: dict) -> 'Session':
        """从字典创建会话对象"""
        session = cls(
            data["session_id"],
            data["session_type"],
            data["self_id"],
            data.get("max_histories", 10)
        )
        session.messages = [
            SimpleMessage.from_dict(msg) 
            for msg in data.get("messages", [])
        ]
        return session

    def meta_info_to_dict(self) -> dict:
        """获取会话的元信息"""
        return {
            "session_id": self.session_id,
            "session_type": self.session_type
        }
    
    def meta_info_to_str(self) -> str:
        """获取会话的元信息字符串"""
        return json.dumps(self.meta_info_to_dict())

class SessionManager:
    def __init__(self):
        self.sessions: Dict[int, Session] = {}

        self._session_dir = Path(os.path.dirname(os.path.abspath(__file__))) / "sessions"
        self._session_dir.mkdir(exist_ok=True)
        self._load_all_sessions()

    def _load_all_sessions(self) -> None:
        """加载所有已保存的会话"""
        for file in self._session_dir.glob("*.json"):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    session = Session.from_dict(data)
                    self.sessions[session.session_id] = session
            except Exception as e:
                logger.error(f"加载会话文件 {file} 失败: {str(e)}")

    def get_session(self, event: MessageEvent) -> Session:
        """根据消息事件获取或创建会话"""
        session_id = event.group_id if isinstance(event, GroupMessageEvent) else event.user_id
        session_type = "group" if isinstance(event, GroupMessageEvent) else "private"
        self_id = event.self_id
        if session_id not in self.sessions:
            logger.debug(f"创建新{session_type}会话: {session_id}")
            self.sessions[session_id] = Session(session_id, session_type, self_id)
        return self.sessions[session_id]

    def add_message(self, event: MessageEvent) -> None:
        """从消息事件添加到会话历史记录"""
        try:
            session = self.get_session(event)
            session.add_message(event)
        except Exception as e:
            logger.opt(exception=e).error(f"添加消息到会话失败: {str(e)}")

    def add_self_message(self, session_id: int, message: SimpleMessage) -> None:
        """添加自己的消息到会话历史记录"""
        if session_id in self.sessions:
            try:
                self.sessions[session_id].add_message(message)
            except Exception as e:
                logger.error(f"添加Bot消息到会话 {session_id} 失败: {str(e)}")
        else:
            logger.warning(f"会话 {session_id} 不存在，无法添加消息")

    def delete_session(self, session_id: int) -> None:
        """删除指定会话"""
        if session_id in self.sessions:
            try:
                session = self.sessions[session_id]
                del self.sessions[session_id]
                session_file = self._session_dir / f"{session.session_type}_{session_id}.json"
                if session_file.exists():
                    session_file.unlink(missing_ok=True)
            except Exception as e:
                logger.error(f"删除会话 {session_id} 失败: {str(e)}")