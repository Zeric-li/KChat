import yaml
import os
import threading
import shutil
import copy
from typing import List, Dict, Any, Optional, Callable, Literal

from ..logger import kchat_logger as logger

class ConfigManager:
    # 默认配置格式
    DEFAULT_CONFIG = {
        "access_control": {
            "admin_id": [],
            "group": {
                "enable_whitelist": True,
                "whitelist": [],
                "blacklist": []
            },
            "user": {
                "enable_whitelist": False,
                "whitelist": [],
                "blacklist": []
            }
        },
        "session": {
            "valid_message_types": ["text"],
            "max_history": 10
        },
        "query_build": {
            "system": {
                "group_chat": "qq_group_chat.yaml",
                "private_chat": "qq_private_chat.yaml"
            },
            "character": "kanade.yaml"
        },
        "llm_api": {
            "api_url": "https://openrouter.ai/api/v1/chat/completions",
            "api_key": "",
            "model": "google/gemma-3-27b-it",
            "timeout": 30,
            "max_retries": 3
        },
        "model_hyperparameters": {
            "temperature": 0.7,
            "max_tokens": 2048
        }
    }

    # 验证配置
    def _validate_config(self, config: Dict[str, Any]) -> None:
        # 验证session部分
        if "session" not in config:
            raise ValueError("缺少session配置节")
        
        session = config["session"]
        required = ["valid_message_types", "max_history"]
        if not all(k in session for k in required):
            raise ValueError("session配置不完整")
        
        if not isinstance(session["max_history"], int) or session["max_history"] <= 0:
            raise ValueError("max_history必须是正整数")
        
        if not isinstance(session["valid_message_types"], list):
            raise ValueError("valid_message_types必须是列表")

        # 验证access_control部分保持不变
        if "access_control" not in config:
            raise ValueError("缺少access_control配置节")

        access = config["access_control"]
        if not isinstance(access.get("admin_id", []), list):
            raise ValueError("admin_id必须是列表")
        
        # 验证群聊权限配置
        self._validate_access_settings(access.get("group", {}), "群聊")
        self._validate_access_settings(access.get("user", {}), "私聊")

        # 验证query_build部分
        if "query_build" not in config:
            raise ValueError("缺少query_build配置节")
        
        query_build = config["query_build"]
        required = ["system", "character"]
        if not all(k in query_build for k in required):
            raise ValueError("query_build配置不完整")

        # 验证llm_api部分
        if "llm_api" not in config:
            raise ValueError("缺少llm_api配置节")
        
        llm_api = config["llm_api"]
        required = ["api_url", "api_key", "model"]
        if not all(k in llm_api for k in required):
            raise ValueError("llm_api配置不完整")

        # 验证model_hyperparameters部分
        if "model_hyperparameters" not in config:
            raise ValueError("缺少model_hyperparameters配置节")

        hyperparams = config["model_hyperparameters"]
        required = ["temperature", "max_tokens"]
        if not all(k in hyperparams for k in required):
            raise ValueError("model_hyperparameters配置不完整")

        # 验证可选参数类型
        optional_params = {
            "seed": int,
            "top_p": float,
            "top_k": int,
            "frequency_penalty": float,
            "presence_penalty": float,
            "repetition_penalty": float,
            "min_p": float,
            "top_a": float
        }

        for param, param_type in optional_params.items():
            if param in hyperparams and not isinstance(hyperparams[param], param_type):
                raise ValueError(f"{param} 必须是 {param_type.__name__} 类型")

    def __init__(self):
        self._config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_settings.yaml")
        self._backup_path = self._config_path + ".bak"  # 备份文件路径
        self._lock = threading.Lock()  # 线程锁
        self._config = self._load_or_create_config()
        self._callbacks = []  # 配置变更回调函数列表

    def _load_or_create_config(self) -> Dict[str, Any]:
        # 尝试从备份恢复
        if not os.path.exists(self._config_path) and os.path.exists(self._backup_path):
            logger.warning(f"主配置文件 {self._config_path} 不存在，尝试从备份 {self._backup_path} 恢复")
            shutil.copyfile(self._backup_path, self._config_path)

        with self._lock:
            if not os.path.exists(self._config_path):
                logger.warning(f"创建默认配置文件 {self._config_path}")
                self._save_config(self.DEFAULT_CONFIG)
                return copy.deepcopy(self.DEFAULT_CONFIG)

            try:
                with open(self._config_path, "r", encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    self._validate_config(config)
                    logger.success(f"成功加载配置文件 {self._config_path}")
                    return config
            except Exception as e:
                logger.error(f"配置文件 {self._config_path} 加载失败: {str(e)}")
                if os.path.exists(self._backup_path):
                    logger.warning(f"尝试从备份文件 {self._backup_path} 恢复配置")
                    return self._load_or_create_config()
                raise

    def _validate_access_settings(self, settings: Dict[str, Any], setting_type: str) -> None:
        if not isinstance(settings.get("enable_whitelist", False), bool):
            raise ValueError(f"{setting_type} enable_whitelist必须是布尔值")
        if not isinstance(settings.get("whitelist", []), list):
            raise ValueError(f"{setting_type} whitelist必须是列表")
        if not isinstance(settings.get("blacklist", []), list):
            raise ValueError(f"{setting_type} blacklist必须是列表")

    def add_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """添加配置变更回调函数"""
        with self._lock:
            self._callbacks.append(callback)

    def _notify_callbacks(self) -> None:
        """通知所有回调函数配置已变更"""
        with self._lock:
            logger.debug(f"开始通知 {len(self._callbacks)} 个回调函数")
            for callback in self._callbacks:
                try:
                    callback(self._config)
                except Exception as e:
                    logger.opt(exception=e).error("配置变更回调执行失败")

    def _save_config(self, config: Dict[str, Any]) -> None:
        with self._lock:
            # 先备份原有配置
            if os.path.exists(self._config_path):
                logger.debug(f"备份配置文件 {self._config_path} -> {self._backup_path}")
                shutil.copyfile(self._config_path, self._backup_path)
            
            with open(self._config_path, "w", encoding='utf-8') as f:
                yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
            logger.info(f"配置文件 {self._config_path} 已更新")

    def reload(self) -> None:
        """重新加载配置文件"""
        with self._lock:
            self._config = self._load_or_create_config()
            self._notify_callbacks()


    """权限控制"""
    # 管理员配置
    @property
    def admin_id(self) -> List[int]:
        """获取管理员ID列表"""
        return self._config["access_control"]["admin_id"]

    def add_admin(self, user_id: int) -> None:
        """添加管理员ID"""
        if user_id in self._config["access_control"]["admin_id"]:
            logger.warning(f"用户 {user_id} 已是管理员")
            return
        self._config["access_control"]["admin_id"].append(user_id)
        self._save_config(self._config)
        logger.info(f"已添加用户 {user_id} 为管理员")

    def remove_admin(self, user_id: int) -> None:
        """移除管理员ID"""
        if user_id not in self._config["access_control"]["admin_id"]:
            logger.warning(f"用户 {user_id} 不是管理员")
            return
        self._config["access_control"]["admin_id"].remove(user_id)
        self._save_config(self._config)
        logger.info(f"已移除用户 {user_id} 的管理员权限")

    # 群聊权限配置
    @property
    def group_enable_whitelist(self) -> bool:
        """获取群聊白名单启用状态"""
        return self._config["access_control"]["group"]["enable_whitelist"]

    def set_group_whitelist(self, enable: bool) -> None:
        """设置群聊白名单状态"""
        self._config["access_control"]["group"]["enable_whitelist"] = enable
        self._save_config(self._config)
        logger.info(f"已设置群聊为 {'白名单' if enable else '黑名单'} 模式")

    @property
    def group_whitelist(self) -> List[int]:
        """获取群聊白名单ID列表"""
        return self._config["access_control"]["group"]["whitelist"]
    
    @property
    def group_blacklist(self) -> List[int]:
        """获取群聊黑名单ID列表"""
        return self._config["access_control"]["group"]["blacklist"]
    
    def add_group(self, list_type: Literal["whitelist", "blacklist"], group_id: int) -> None:
        """添加群聊ID到黑/白名单"""
        if group_id in self._config["access_control"]["group"][list_type]:
            logger.warning(f"群聊 {group_id} 已在 {'白名单' if list_type == "whitelist" else '黑名单'} 中")
            return
        self._config["access_control"]["group"][list_type].append(group_id)
        self._save_config(self._config)
        logger.info(f"已添加群聊 {group_id} 到 {'白名单' if list_type == "whitelist" else '黑名单'}")

    def remove_group(self, list_type: Literal["whitelist", "blacklist"], group_id: int) -> None:
        """从黑/白名单中移除群聊ID"""
        if group_id not in self._config["access_control"]["group"][list_type]:
            logger.warning(f"群聊 {group_id} 不在 {'白名单' if list_type == "whitelist" else '黑名单'} 中")
            return
        self._config["access_control"]["group"][list_type].remove(group_id)
        self._save_config(self._config)
        logger.info(f"已从 {'白名单' if list_type == "whitelist" else '黑名单'} 中移除群聊 {group_id}")

    # 私聊权限配置
    @property
    def private_enable_whitelist(self) -> bool:
        """获取私聊白名单启用状态"""
        return self._config["access_control"]["user"]["enable_whitelist"]

    def set_private_whitelist(self, enable: bool) -> None:
        """设置私聊白名单状态"""
        self._config["access_control"]["user"]["enable_whitelist"] = enable
        self._save_config(self._config)
        logger.info(f"已设置私聊为 {'白名单' if enable else '黑名单'} 模式")

    @property
    def private_whitelist(self) -> List[int]:
        """获取私聊白名单ID列表"""
        return self._config["access_control"]["user"]["whitelist"]

    @property
    def private_blacklist(self) -> List[int]:
        """获取私聊黑名单ID列表"""
        return self._config["access_control"]["user"]["blacklist"]
    
    def add_user(self, list_type: Literal["whitelist", "blacklist"], user_id: int) -> None:
        """添加用户ID到黑/白名单"""
        if user_id in self._config["access_control"]["user"][list_type]:
            logger.warning(f"用户 {user_id} 已在 {'白名单' if list_type == "whitelist" else '黑名单'} 中")
            return
        self._config["access_control"]["user"][list_type].append(user_id)
        self._save_config(self._config)
        logger.info(f"已添加用户 {user_id} 到 {'白名单' if list_type == "whitelist" else '黑名单'}")

    def remove_user(self, list_type: Literal["whitelist", "blacklist"], user_id: int) -> None:
        """从黑/白名单中移除用户ID"""
        if user_id not in self._config["access_control"]["user"][list_type]:
            logger.warning(f"用户 {user_id} 不在 {'白名单' if list_type == "whitelist" else '黑名单'} 中")
            return
        self._config["access_control"]["user"][list_type].remove(user_id)
        self._save_config(self._config)
        logger.info(f"已从 {'白名单' if list_type == "whitelist" else '黑名单'} 中移除用户 {user_id}")


    """会话配置"""
    @property
    def valid_message_types(self) -> List[str]:
        return self._config["session"]["valid_message_types"]

    @property
    def max_history(self) -> int:
        return self._config["session"]["max_history"]

    def set_max_history(self, value: int) -> None:
        if not isinstance(value, int) or value <= 0:
            raise ValueError("max_history必须是正整数")
        if value > 30:
            logger.warning("max_history设置过大，可能导致性能问题，已调整为30")
            value = 30
        self._config["session"]["max_history"] = value
        self._save_config(self._config)
        logger.info(f"已设置最大历史消息数为 {value}")

    """模型提示词配置"""
    @property
    def system_prompts_dir(self) -> Dict[str, str]:
        """获取系统提示词文件路径，返回字典包含group_chat和private_chat路径"""
        base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../prompt/system/")
        return {
            "group_chat": os.path.join(base_path, self._config["query_build"]["system"]["group_chat"]),
            "private_chat": os.path.join(base_path, self._config["query_build"]["system"]["private_chat"])
        }

    @property
    def character_prompt_dir(self) -> str:
        """获取角色提示词文件路径"""
        base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../prompt/character/")
        return os.path.join(base_path, self._config["query_build"]["character"])

    def _load_character_data(self) -> Dict[str, Any]:
        """加载角色数据"""
        character_file = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../prompt/character/",
            self._config["query_build"]["character"]
        ))
        try:
            with open(character_file, "r", encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
                if data.get("alias") is None:
                    data["alias"] = []
                return {
                    "name": data.get("name", "未知角色"),
                    "alias": data.get("alias", []),
                    "chracter_info": data.get("chracter_info", "")
                }
        except Exception as e:
            logger.opt(exception=e).error(f"加载角色文件 {character_file} 失败")
            raise

    def _save_character_data(self, data: Dict[str, Any]) -> None:
        """保存角色数据"""
        character_file = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../prompt/character/",
            self._config["query_build"]["character"]
        ))
        os.makedirs(os.path.dirname(character_file), exist_ok=True)
        try:
            # 确保数据格式正确
            if data.get("alias") is None:
                data["alias"] = []
                
            with open(character_file, "w", encoding='utf-8') as f:
                # 写入角色名称部分
                f.write("# 角色名称\n")
                f.write(f"name: {data.get('name', '未知角色')}\n\n")
                
                # 写入角色别名部分
                f.write("# 角色别称\n")
                f.write("alias:\n")
                for alias in data.get("alias", []):
                    if alias:  # 确保别名非空
                        f.write(f"  - {alias}\n")
                f.write("\n")
                
                # 写入chracter_info部分
                f.write("# 人设内容\n")
                if "chracter_info" in data:
                    f.write("chracter_info: |-\n")
                    for line in data["chracter_info"].split('\n'):
                        f.write(f"  {line}\n")
                
            logger.info(f"角色文件 {character_file} 已更新")
        except Exception as e:
            logger.opt(exception=e).error(f"保存角色文件 {character_file} 失败")
            raise

    @property
    def character_name(self) -> str:
        """获取角色名称"""
        character_data = self._load_character_data()
        name = character_data.get("name")
        if not name:
            logger.warning(f"角色文件中未定义name字段")
            return "未知角色"
        return name

    @property
    def character_alias(self) -> List[str]:
        """获取角色别名"""
        character_data = self._load_character_data()
        return character_data.get("alias", [])

    def add_character_alias(self, alias: str) -> None:
        """添加角色别名"""
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("别名必须是非空字符串")
            
        character_data = self._load_character_data()
        aliases = character_data.setdefault("alias", [])
        if aliases is None:
            aliases = []
            character_data["alias"] = aliases
        if alias not in aliases:
            aliases.append(alias)
            self._save_character_data(character_data)
            logger.info(f"已添加角色别名: {alias}")

    def remove_character_alias(self, alias: str) -> None:
        """移除角色别名"""
        character_data = self._load_character_data()
        aliases = character_data.get("alias", [])
        if alias in aliases:
            aliases.remove(alias)
            self._save_character_data(character_data)
            logger.info(f"已移除角色别名: {alias}")
        else:
            logger.warning(f"尝试移除不存在的别名: {alias}")

    def clear_character_aliases(self) -> None:
        """清空角色别名"""
        character_data = self._load_character_data()
        if "alias" in character_data:
            character_data["alias"] = []
            self._save_character_data(character_data)
            logger.info("已清空所有角色别名")

    @property
    def character_info(self) -> str:
        """获取角色信息"""
        character_data = self._load_character_data()
        return character_data.get("chracter_info", "")

    """大模型接口配置"""
    @property
    def api_url(self) -> str:
        return self._config["llm_api"]["api_url"]
    
    @property 
    def api_key(self) -> str:
        return self._config["llm_api"]["api_key"]
    
    @property
    def model(self) -> str:
        return self._config["llm_api"]["model"]

    # （可选）超时
    @property
    def api_timeout(self) -> Optional[int]:
        return self._config["llm_api"].get("timeout")

    # （可选）最大重试次数
    @property
    def api_retry_times(self) -> Optional[int]:
        return self._config["llm_api"].get("retry_times")

    """模型超参数配置"""
    # 必须超参数属性
    @property
    def temperature(self) -> float:
        return self._config["model_hyperparameters"]["temperature"]

    @property
    def max_tokens(self) -> int:
        return self._config["model_hyperparameters"]["max_tokens"]

    # 可选超参数属性
    @property
    def seed(self) -> Optional[int]:
        return self._config["model_hyperparameters"].get("seed")

    @property
    def top_p(self) -> Optional[float]:
        return self._config["model_hyperparameters"].get("top_p")

    @property
    def top_k(self) -> Optional[int]:
        return self._config["model_hyperparameters"].get("top_k")

    @property
    def frequency_penalty(self) -> Optional[float]:
        return self._config["model_hyperparameters"].get("frequency_penalty")

    @property
    def presence_penalty(self) -> Optional[float]:
        return self._config["model_hyperparameters"].get("presence_penalty")

    @property
    def repetition_penalty(self) -> Optional[float]:
        return self._config["model_hyperparameters"].get("repetition_penalty")

    @property
    def min_p(self) -> Optional[float]:
        return self._config["model_hyperparameters"].get("min_p")

    @property
    def top_a(self) -> Optional[float]:
        return self._config["model_hyperparameters"].get("top_a")


if __name__ == "__main__":
    config_manager = ConfigManager()
    print("=== 配置测试 ===")
    print(f"系统提示词路径 - 群聊: {os.path.abspath(config_manager.system_prompts_dir['group_chat'])}")
    print(f"系统提示词路径 - 私聊: {os.path.abspath(config_manager.system_prompts_dir['private_chat'])}")
    print(f"角色提示词路径: {os.path.abspath(config_manager.character_prompt_dir)}")
    print(f"API URL: {config_manager.api_url}")
    print(f"模型: {config_manager.model}")
    print(f"温度: {config_manager.temperature}")
    print(f"最大token数: {config_manager.max_tokens}")
    
    # 新增角色提示词文件测试项
    print("\n=== 角色提示词文件测试 ===")
    print(f"角色名称: {config_manager.character_name}")
    print(f"当前角色别称: {config_manager.character_alias}")
    print(f"当前人设内容: \n{config_manager.character_info}")