import json
import aiohttp
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List, Literal, Union
from aiohttp import ClientTimeout
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..logger import kchat_logger as logger
from ..config import config_manager
from ..session_context import Session
from .query_builder import QueryBuilder

class ApiClient:
    def __init__(self):
        self.config_manager = config_manager
        self.query_builder = QueryBuilder()
        self.timeout = ClientTimeout(total=getattr(config_manager, 'api_timeout', 30))  # 超时时间，默认30秒
        self.max_retries = getattr(config_manager, 'max_retries', 3)  # 最大重试次数，默认3次

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def chat_completion(self, session: Session) -> Optional[str]:
        """与大模型API交互"""
        try:
            # 构建完整请求数据
            start_time = asyncio.get_event_loop().time()
            payload = self._build_request_payload(session)
            
            # 发送请求
            headers = {
                "Authorization": f"Bearer {self.config_manager.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            async with aiohttp.ClientSession(timeout=self.timeout) as client_session:
                async with client_session.post(
                    self.config_manager.api_url,
                    headers=headers,
                    json=payload
                ) as response:
                    response_text = await response.text()

                    # 检查响应状态码
                    if response.status == 200:
                        try:
                            result = await response.json()
                        except json.JSONDecodeError:
                            logger.error(f"API响应非JSON格式: {response_text[:500]}...")
                            return None
                        elapsed = asyncio.get_event_loop().time() - start_time
                        content = self._extract_response_content(result)
                        logger.debug(f"API请求成功，耗时: {elapsed:.2f}s")
                        return content
                    else:
                        error_text = await response.text()
                        logger.error(f"API请求失败 - 状态码: {response.status}, 响应: {error_text}")
                        return None
                        
        except asyncio.TimeoutError:
            logger.warning("API请求超时")
            raise
        except aiohttp.ClientError as e:
            logger.opt(exception=e).error(f"API请求网络错误 - URL: {self.config_manager.api_url}")
            raise
        except json.JSONDecodeError as e:
            logger.opt(exception=e).error("API响应JSON解析失败")
            return None
        except Exception as e:
            logger.opt(exception=e).error("处理API响应时发生意外错误")
            return None

    def _build_request_payload(self, session: Session) -> Dict[str, Any]:
        """构建完整的API请求payload"""
        messages = self.query_builder.build_request(session)
        
        payload = {
            "messages": messages,
            "model": self.config_manager.model,
            "temperature": self.config_manager.temperature,
            "max_tokens": self.config_manager.max_tokens,
            "stream": False  # 默认关闭流式输出
        }
        
        # 添加可选参数
        optional_params = {
            k: v for k, v in {
                "seed": self.config_manager.seed,
                "top_p": self.config_manager.top_p,
                "top_k": self.config_manager.top_k,
                "frequency_penalty": self.config_manager.frequency_penalty,
                "presence_penalty": self.config_manager.presence_penalty,
                "repetition_penalty": self.config_manager.repetition_penalty,
                "min_p": self.config_manager.min_p,
                "top_a": self.config_manager.top_a
                # "stop": self.config_manager.stop_sequences,
                # "tools": self.config_manager.tools,
                # "tool_choice": self.config_manager.tool_choice
            }.items() if v is not None
        }
        
        # 过滤掉None值
        payload.update(optional_params)
        return payload

    def _extract_response_content(self, result: Dict[str, Any]) -> str:
        """从API响应中提取内容"""
        try:
            if not result.get("choices"):
                logger.error(f"API响应缺少choices字段，完整响应: {json.dumps(result, indent=2, ensure_ascii=False)[:1000]}...")
                return ""
                
            choice = result["choices"][0]
            if choice.get("finish_reason") == "length":
                logger.warning(f"API响应被截断，完整响应: {json.dumps(result, indent=2, ensure_ascii=False)[:1000]}...")
            
            if choice.get("message"):
                logger.debug(f"API响应成功，完整响应: {json.dumps(result, indent=2, ensure_ascii=False)[:1000]}...")
                return choice["message"].get("content", "")
            elif choice.get("delta"):
                logger.debug(f"API响应成功，完整响应: {json.dumps(result, indent=2, ensure_ascii=False)[:1000]}...")
                return choice["delta"].get("content", "")
            return ""
        except Exception as e:
            logger.opt(exception=e).error("解析API响应内容失败")
            return ""