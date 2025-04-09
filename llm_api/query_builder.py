import json
import os
import yaml
from datetime import datetime
from typing import List, Dict, Any, Union, Literal

from ..logger import kchat_logger as logger
from ..config import config_manager
from ..session_context import Session

class PromptMask:
    CHARACTER_NAME = config_manager.character_name  # 角色名称，{name}
    CHARACTER_ALIAS = "、".join(config_manager.character_alias) if config_manager.character_alias else "无"  # 角色别名，{alias}
    CHARACTER_INFO = config_manager.character_info  # 角色信息，{chracter_info}
    TIME = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 时间，{time}

class MessageContent:
    def __init__(self, msg_type: Literal["text", "image_url"], data: Union[str, Dict[str, str]]):
        self.type = msg_type
        self.data = data

    def to_api_format(self) -> Dict[str, Any]:
        """转换为API需要的格式"""
        if self.type == "text":
            return {"type": "text", "text": self.data}
        return {
            "type": "image_url",
            "image_url": {
                "url": self.data["url"] if isinstance(self.data, dict) and "url" in self.data else "",
                "detail": self.data["detail"] if isinstance(self.data, dict) and "detail" in self.data else "auto"
            }
        }

    def __repr__(self) -> str:
        return f"MessageContent(type={self.type}, data={self.data})"

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {"type": self.type, self.type: self.data}

    @classmethod
    def text(cls, content: str) -> 'MessageContent':
        """快速创建文本消息"""
        return cls("text", content)

    @classmethod
    def image(cls, url: str, detail: str = "auto") -> 'MessageContent':
        """快速创建图片消息"""
        return cls("image_url", {"url": url, "detail": detail})

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MessageContent':
        """从字典反序列化"""
        if data["type"] not in ("text", "image_url"):
            raise ValueError("msg_type必须是'text'或'image_url'")
        return cls(data["type"], data[data["type"]])

class Message:
    def __init__(self, role: Literal["user", "assistant", "system"], content: Union[str, MessageContent, List[MessageContent]]):
        self.role = role
        self.content = content

    def to_api_format(self) -> Dict[str, Any]:
        """转换为API需要的完整消息格式"""
        if isinstance(self.content, str):
            return {"role": self.role, "content": self.content}
        
        if isinstance(self.content, MessageContent):
            return {"role": self.role, "content": [self.content.to_api_format()]}
            
        return {
            "role": self.role, 
            "content": [item.to_api_format() for item in self.content]
        }

    def __repr__(self) -> str:
        return f"Message(role={self.role}, content={self.content})"

    @classmethod
    def system(cls, prompt: str) -> 'Message':
        """快速创建系统消息"""
        return cls("system", prompt)

    @classmethod
    def user(cls, content: Union[str, MessageContent, List[MessageContent]]) -> 'Message':
        """快速创建用户消息"""
        return cls("user", content)

    @classmethod
    def assistant(cls, content: Union[str, MessageContent]) -> 'Message':
        """快速创建助手消息"""
        return cls("assistant", content)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        if isinstance(self.content, str):
            return {"role": self.role, "content": self.content}
        elif isinstance(self.content, MessageContent):
            return {"role": self.role, "content": self.content.to_dict()}
        else:
            raise ValueError("content必须是str或MessageContent")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Message':
        """从字典反序列化"""
        role = data["role"]
        content = data["content"]
        if isinstance(content, str):
            return cls(role, content)
        elif isinstance(content, dict):
            return cls(role, MessageContent.from_dict(content))
        else:
            raise ValueError("content必须是str或MessageContent")

class SystemPromptBuilder:
    def __init__(self):
        self.system_message = Message.system("")  # 使用Message类初始化
        self.session_type = ""

    def set_system_prompt(self, session_type) -> None:
        """设置系统提示词"""
        self.session_type = session_type
        
        # 检查配置管理器是否包含必要的属性
        if not hasattr(config_manager, 'system_prompts_dir'):
            logger.error("配置管理器中缺少system_prompts_dir配置")
            self.system_message = Message.system("")
            return

        # 确定提示词文件路径
        prompt_key = 'private_chat' if session_type == "private" else 'group_chat'

        # 加载系统提示词文件
        try:
            # 从配置管理器获取提示词目录
            prompt_dir = config_manager.system_prompts_dir
            if not isinstance(prompt_dir, dict) or prompt_key not in prompt_dir:
                raise ValueError(f"无效的提示词目录配置: {prompt_dir}")
            
            prompt_file = prompt_dir[prompt_key]
            if not os.path.exists(prompt_file):
                raise FileNotFoundError(f"提示词文件不存在: {prompt_file}")
            
            # 加载提示词文件
            with open(prompt_file, 'r', encoding='utf-8') as f:
                prompt_data = yaml.safe_load(f)
                
            if not prompt_data or 'system' not in prompt_data:
                raise ValueError("提示词文件格式不正确，缺少system字段")

            prompt_text = prompt_data['system']
            if not isinstance(prompt_text, str):
                raise ValueError("system提示词必须是字符串类型")
                
            # 替换蒙版关键词
            self.system_message = Message.system(
                prompt_text
                .replace("{name}", PromptMask.CHARACTER_NAME)
                .replace("{alias}", PromptMask.CHARACTER_ALIAS)
                .replace("{chracter_info}", PromptMask.CHARACTER_INFO)
                .replace("{time}", PromptMask.TIME)
            )
        except Exception as e:
            logger.opt(exception=e).error("加载系统提示词失败")
            # 使用默认提示词作为回退
            default_prompt = (
                f"你正在扮演{PromptMask.CHARACTER_NAME}进行{'私聊' if session_type == 'private' else '群聊'}对话"
            )
            self.system_message = Message.system(default_prompt)


    def get_system_prompt(self) -> Message:
        """获取系统消息对象"""
        return self.system_message

class UserPromptBuilder:
    def __init__(self):
        self.user_prompt = Message.user([])  # 使用Message类初始化
        self.session_type = ""

    def set_user_prompt(self, session: Session) -> None:
        """设置用户提示词"""
        self.session_type = session.session_type
        message_contents = []

        text_parts = []

        # 构建会话信息头
        text_parts.extend([
            f"{'群聊' if self.session_type == 'group' else '私聊'}ID：{session.session_id}",
            "聊天记录："
        ])

        # 处理消息记录
        for message in session.messages:
            # 添加消息头
            text_parts.append(f"\n\n**{message.user_name}**({message.user_id}) | {datetime.fromtimestamp(message.time).strftime("%Y-%m-%d %H:%M:%S")}")
            # 遍历消息段
            for message_segment in message.message:
                # 处理文本内容
                if message_segment.type == "text":
                    # 检查 data 是否为字符串类型
                    if isinstance(message_segment.data, str):
                        text_content = message_segment.data.strip()
                        if text_content:
                            text_parts.append(f"\n{text_content}")
                    
                # 处理图片内容
                elif message_segment.type == "image_url":
                    # 处理累积的文本
                    if text_parts:
                        message_contents.append(MessageContent.text("".join(text_parts)))
                        text_parts = []
                    # 处理图片
                    url = message_segment.data.get("url", "") if isinstance(message_segment.data, dict) else ""
                    if url.startswith(("http://", "https://")):
                        detail = message_segment.data.get("detail", "auto") if isinstance(message_segment.data, dict) else "auto"
                        message_contents.append(MessageContent.image(url, detail))

        # 处理最后剩余的文本
        if text_parts:
            message_contents.append(MessageContent.text("".join(text_parts)))

        # 添加模型输入提示
        message_contents.append(MessageContent.text("\n\n# Chat Textbox"))

        # 构建完整用户消息
        self.user_prompt = Message.user(message_contents)

    def get_user_prompt(self) -> Message:
        """获取完整的用户消息对象"""
        return self.user_prompt

    def get_user_prompt_str(self) -> str:
        """获取用户提示词JSON字符串"""
        return json.dumps(
            self.user_prompt.to_api_format()["content"],
            ensure_ascii=False
        )


# class AssistantResponseHeaderBuilder:
#     def __init__(self):
#         self.assistant_response_header = Message.assistant("")  # 使用Message类初始化
#         self.session_type = ""

#     def set_assistant_response_header(self, session: Session) -> None:
#         """设置助手提示词头"""
#         if not session or not hasattr(session, 'self_id'):
#             logger.error("无效的session对象")
#             return
            
#         try:
#             self.assistant_response_header = Message.assistant(
#                 f"\n# Chat Textbox\n"
#             )
#             self.session_type = session.session_type
#         except Exception as e:
#             logger.error(f"设置助手提示词头失败: {str(e)}")

#     def get_assistant_prompt_header(self) -> Message:
#         """获取助手消息头对象"""
#         return self.assistant_response_header

#     def get_assistant_prompt_header_str(self) -> str:
#         """获取助手提示词头JSON字符串"""
#         return json.dumps(
#             self.assistant_response_header.to_api_format()["content"],
#             ensure_ascii=False
#         )


class QueryBuilder:
    def __init__(self):
        self.system_prompt_builder = SystemPromptBuilder()
        self.user_prompt_builder = UserPromptBuilder()
        # self.assistant_response_header_builder = AssistantResponseHeaderBuilder()

        self._init_query_log_dir()

    def _init_query_log_dir(self) -> None:
        """初始化Query日志目录"""
        self.log_dir = os.path.join(os.path.dirname(__file__), "last_queries")
        os.makedirs(self.log_dir, exist_ok=True)

    def _save_query_to_file(self, session: Session, request_data: List[Dict[str, Any]]) -> None:
        """将Query数据保存到文件"""
        # 清理旧文件
        for filename in os.listdir(self.log_dir):
            if filename.startswith(f"{session.session_type}_{session.session_id}"):
                os.remove(os.path.join(self.log_dir, filename))
        
        # 保存新文件
        filename = f"{session.session_type}_{session.session_id}.json"
        filepath = os.path.join(self.log_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(request_data, f, ensure_ascii=False, indent=2)

    def build_request(self, session: Session) -> List[Dict[str, Any]]:
        """构建组合消息(系统+用户+助手)
        
        返回格式示例:
        [
            {
                "role": "system",
                "content": "系统提示词..."
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": "用户消息..."
                    },
                    {
                        "type": "image_url", 
                        "image_url": {
                            "url": "...", 
                            "detail": "auto"
                        }
                    }
                ]
            },
            {
                "role": "assistant",
                "content": "助手消息头..." 
            }
        ]
        """
        # 构建系统提示词
        self.system_prompt_builder.set_system_prompt(session.session_type)
        system_message = self.system_prompt_builder.get_system_prompt()
        
        # 构建用户提示词
        self.user_prompt_builder.set_user_prompt(session)
        user_message = self.user_prompt_builder.get_user_prompt()

        # # 构建助手消息头
        # self.assistant_response_header_builder.set_assistant_response_header(session)
        # assistant_response_header = self.assistant_response_header_builder.get_assistant_prompt_header()
        
        # 组合消息
        request_data = [
            system_message.to_api_format(),
            user_message.to_api_format(),
            # assistant_response_header.to_api_format() if (assistant_response_header or assistant_response_header.content) else None
        ]
        
        # 保存到文件
        self._save_query_to_file(session, request_data)
        
        return request_data
    def build_request_str(self, session: Session) -> str:
        """构建组合消息JSON字符串"""
        return json.dumps(
            self.build_request(session),
            ensure_ascii=False,
            indent=2
        )
