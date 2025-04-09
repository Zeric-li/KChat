from nonebot import on_message, on_command
from nonebot.adapters.onebot.v11 import (
    MessageEvent,
    PrivateMessageEvent,
    GroupMessageEvent,
    Message,
    MessageSegment
)
from nonebot.matcher import Matcher
from nonebot.params import Depends, CommandArg
from nonebot.rule import Rule
from typing import Dict, Any, Optional, List, Literal, Union, Set
from collections import defaultdict
from datetime import datetime
import asyncio

from .logger import kchat_logger as logger
from .session_context import Session, SimpleMessage, session_manager
from .llm_api import ApiClient
from .config import config_manager

# 初始化API客户端
api_client = ApiClient()

# 注册消息处理器
message = on_message()

# 定义指令处理器
clear_cmd = on_command("clear", aliases={"清除记录"}, priority=1)
# undo_cmd = on_command("undo", aliases={"撤回"}, priority=10)

# 定义会话状态
class SessionState:
    def __init__(self):
        self.last_message_time = defaultdict(float)
        self.pending_sessions: Set[int] = set()
        
    def update_time(self, session_id: int):
        self.last_message_time[session_id] = asyncio.get_event_loop().time()
        
    def add_pending(self, session_id: int):
        self.pending_sessions.add(session_id)
        
    def remove_pending(self, session_id: int):
        self.pending_sessions.discard(session_id)
        
    def is_pending(self, session_id: int) -> bool:
        return session_id in self.pending_sessions

# 初始化会话状态
session_state = SessionState()

@clear_cmd.handle()
async def handle_clear(event: MessageEvent):
    """处理/clear指令"""
    session = session_manager.get_session(event)
    session.clear_history()
    await clear_cmd.finish("聊天记录已清除")

# @undo_cmd.handle()
# async def handle_undo(event: MessageEvent, matcher: Matcher):
#     """处理/undo指令"""
#     session = session_manager.get_session(event)
#     session_id = event.group_id if isinstance(event, GroupMessageEvent) else event.user_id
    
#     # 尝试撤回上一条机器人消息
#     if session.messages and session.messages[-1].user_id == event.self_id:
#         removed_msg = session.messages.pop()
#         session._save_session()
        
#         # 尝试通过API撤回消息
#         if isinstance(event, GroupMessageEvent):
#             try:
#                 await matcher.send(MessageSegment.text("已撤回上一条消息"))
#                 # 实际撤回逻辑需要根据OneBot实现
#                 # 这里只是示例，实际需要记录message_id才能撤回
#             except Exception as e:
#                 logger.error(f"撤回消息失败: {str(e)}")
#         await undo_cmd.finish("已撤回上一条消息")
#     else:
#         await undo_cmd.finish("没有可撤回的消息")

def check_access(event: MessageEvent) -> bool:
    """检查消息是否允许处理"""
    # 跳过指令消息
    if event.raw_message.strip().startswith(("/", "!", ".")):
        logger.opt(colors=True).debug(f"[Access] <yellow>CMD_IGNORE</yellow> | User:{event.user_id} | Content:{event.raw_message[:50]}...")
        return False
    
    if isinstance(event, PrivateMessageEvent):
        # 管理员直接放行
        if event.user_id in config_manager.admin_id:
            logger.opt(colors=True).debug(f"[Access] <green>ADMIN_PASS</green> | User:{event.user_id} | Content:{event.raw_message[:50]}...")
            return True
        # 私聊消息检查黑/白名单
        if config_manager.private_enable_whitelist:
            access = event.user_id in config_manager.private_whitelist
            logger.opt(colors=True).debug(f"[Access] {'<green>PRIVATE_WHITELIST_PASS</green>' if access else '<red>PRIVATE_WHITELIST_DENY</red>'} | User:{event.user_id} | Content:{event.raw_message[:50]}...")
            return access
        access = event.user_id not in config_manager.private_blacklist
        logger.opt(colors=True).debug(f"[Access] {'<green>PRIVATE_BLACKLIST_PASS</green>S' if access else '<red>PRIVATE_BLACKLIST_DENY</red>'} | User:{event.user_id} | Content:{event.raw_message[:50]}...")
        return access
    
    elif isinstance(event, GroupMessageEvent):
        # 群聊消息检查黑/白名单
        if config_manager.group_enable_whitelist:
            access = event.group_id in config_manager.group_whitelist
            logger.opt(colors=True).debug(f"[Access] {'<green>GROUP_WHITELIST_PASS</green>' if access else '<red>GROUP_WHITELIST_DENY</red>'} | Group:{event.group_id} | User:{event.user_id} | Content:{event.raw_message[:50]}...")
            return access
        access = event.group_id not in config_manager.group_blacklist
        logger.opt(colors=True).debug(f"[Access] {'<green>GROUP_BLACKLIST_PASS</green>' if access else '<red>GROUP_BLACKLIST_DENY</red>'} | Group:{event.group_id} | User:{event.user_id} | Content:{event.raw_message[:50]}...")
        return access
    
    return False

def check_mention(event: MessageEvent) -> bool:
    """检查是否提及机器人"""
    # 先检查权限
    if not check_access(event):
        return False
        
    # 私聊消息直接返回True
    if isinstance(event, PrivateMessageEvent):
        return True
    
    # 群聊消息检查@消息
    if event.is_tome():
        return True
    
    # 检查消息文本是否包含机器人名称或别名
    msg_text = event.get_plaintext().strip()
    bot_names = {config_manager.character_name, *config_manager.character_alias}
    
    # 预处理消息文本和关键词
    msg_words = msg_text.split()
    for name in bot_names:
        if not name:
            continue
            
        # 英文关键词处理（大小写不敏感）
        if name.isascii():
            name_lower = name.lower()
            # 检查是否作为独立单词出现
            if any(word.lower() == name_lower for word in msg_words):
                return True
        # 中文关键词处理（精确匹配）
        else:
            if name in msg_text:
                return True
                
    return False

async def handle_llm_query(event: MessageEvent, session: Session) -> List[str]:
    """处理LLM查询并返回分割后的消息列表"""
    try:
        # 构建并发送查询
        response = await api_client.chat_completion(session)
        if not response:
            logger.error("LLM API返回空响应")
            return []
        
        logger.debug(f"原始回复消息: {response}")
        # 按换行符分割并过滤空行
        return [line.strip() for line in response.split('\n') if line.strip()]
    except Exception as e:
        logger.opt(exception=e).error("处理LLM查询时出错")
        return []

async def delayed_llm_query(event: MessageEvent, session: Session, matcher: Matcher):
    """延迟处理LLM查询"""
    session_id = event.group_id if isinstance(event, GroupMessageEvent) else event.user_id
    
    # 标记会话为等待状态
    session_state.add_pending(session_id)
    session_state.update_time(session_id)
    logger.debug(f"开始延迟处理会话 {session_id}")
    
    try:
        while True:
            # 等待3秒或直到有新消息
            await asyncio.sleep(5)
            if asyncio.get_event_loop().time() - session_state.last_message_time[session_id] >= 3:
                break
                
        # 执行查询
        logger.info(f"开始处理会话 {session_id} 的LLM查询")
        messages = await handle_llm_query(event, session)
        if not messages:
            logger.warning(f"会话 {session_id} 的LLM查询返回空结果")
            return
            
        # 发送回复
        logger.debug(f"准备发送会话 {session_id} 的回复消息")
        first_msg = messages[0]
        reply = Message(MessageSegment.text(first_msg))
        await matcher.send(reply)
        
        for msg in messages[1:]:
            delay = max(0.75, min(1.5, len(msg) / 60))
            await asyncio.sleep(delay)
            await matcher.send(MessageSegment.text(msg))
            
        # 记录机器人消息
        bot_message = SimpleMessage.from_dict({
            "user_name": config_manager.character_name,
            "user_id": str(event.self_id),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": [{"type": "text", "text": msg} for msg in messages]
        })
        session_manager.add_self_message(
            event.group_id if isinstance(event, GroupMessageEvent) else event.user_id,
            bot_message
        )
        logger.success(f"已完成会话 {session_id} 的消息处理和记录")
        
    except asyncio.CancelledError:
        logger.debug(f"会话 {session_id} 的延迟处理被取消")
    except Exception as e:
        logger.opt(exception=e).error(f"处理会话 {session_id} 出错")
    finally:
        session_state.remove_pending(session_id)

@message.handle()
async def handle_all_message(event: MessageEvent, matcher: Matcher):
    """处理所有消息"""
    try:   
        # 检查权限
        if not check_access(event):
            return
        
        # 获取或创建会话并添加消息
        session = session_manager.get_session(event)
        session.add_message(event)

        # 更新最后消息时间
        session_id = event.group_id if isinstance(event, GroupMessageEvent) else event.user_id
        session_state.update_time(session_id)
        
        # 检查是否需要触发LLM查询
        if check_mention(event) and not session_state.is_pending(session_id):
            # 创建延迟任务
            asyncio.create_task(delayed_llm_query(event, session, matcher))
                
    except Exception as e:
        logger.opt(exception=e).error(f"消息处理失败: {str(e)}")

logger.opt(colors=True).success("<green>KChat 插件初始化完成</green>")