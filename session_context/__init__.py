from .session_manager import SimpleMessageContent, SimpleMessage, Session, SessionManager

# 初始化会话管理器
session_manager = SessionManager()

__all__ = [
    'SimpleMessageContent',
    'SimpleMessage',
    'Session',
    'SessionManager',
    'session_manager'
]