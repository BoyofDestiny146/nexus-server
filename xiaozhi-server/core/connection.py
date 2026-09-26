import os
import sys
import copy
import json
import re
import uuid
import time
import queue
import asyncio
import threading
import traceback
import subprocess
import websockets
from core.utils.util import (
    extract_json_from_string,
    check_vad_update,
    check_asr_update,
    filter_sensitive_info,
)
from typing import Dict, Any
from collections import deque
from core.utils.modules_initialize import (
    initialize_modules,
    initialize_tts,
    initialize_asr,
)
from core.handle.reportHandle import report
from core.providers.tts.default import DefaultTTS
from concurrent.futures import ThreadPoolExecutor
from core.utils.dialogue import Message, Dialogue
from core.providers.asr.dto.dto import InterfaceType
from core.handle.textHandle import handleTextMessage
from core.providers.tools.unified_tool_handler import UnifiedToolHandler
from plugins_func.loadplugins import auto_import_modules
from plugins_func.register import Action, ActionResponse
from core.auth import AuthMiddleware, AuthenticationError
from config.config_loader import get_private_config_from_api
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
from config.logger import setup_logging, build_module_string, create_connection_logger
from config.manage_api_client import DeviceNotFoundException, DeviceBindException
from core.utils.prompt_manager import PromptManager
from core.utils.voiceprint_provider import VoiceprintProvider
from core.utils import textUtils

TAG = __name__

auto_import_modules("plugins_func.functions")


class TTSException(RuntimeError):
    pass


class ConnectionHandler:
    def __init__(
        self,
        config: Dict[str, Any],
        _vad,
        _asr,
        _llm,
        _memory,
        _intent,
        server=None,
    ):
        self.common_config = config
        self.config = copy.deepcopy(config)
        self.session_id = str(uuid.uuid4())
        self.logger = setup_logging()
        self.server = server  # 保存server实例的引用

        self.auth = AuthMiddleware(config)
        self.need_bind = False
        self.bind_code = None
        self.read_config_from_api = self.config.get("read_config_from_api", False)

        self.websocket = None
        self.headers = None
        self.device_id = None
        self.client_ip = None
        self.cc_response_length = "brief"  # careconnect per-device verbosity
        self.prompt = None
        self.welcome_msg = None
        self.max_output_size = 0
        self.chat_history_conf = 0
        self.audio_format = "opus"

        # 客户端状态相关
        self.client_abort = False
        self.client_is_speaking = False
        self.client_listen_mode = "auto"

        # 线程任务相关
        self.loop = asyncio.get_event_loop()
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=5)

        # 添加上报线程池
        self.report_queue = queue.Queue()
        self.report_thread = None
        # 未来可以通过修改此处，调节asr的上报和tts的上报，目前默认都开启
        self.report_asr_enable = self.read_config_from_api
        self.report_tts_enable = self.read_config_from_api

        # 依赖的组件
        self.vad = None
        self.asr = None
        self.tts = None
        self._asr = _asr
        self._vad = _vad
        self.llm = _llm
        self.memory = _memory
        self.intent = _intent

        # 为每个连接单独管理声纹识别
        self.voiceprint_provider = None

        # vad相关变量
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.last_activity_time = 0.0  # 统一的活动时间戳（毫秒）
        self.client_voice_stop = False
        self.client_voice_window = deque(maxlen=5)
        self.last_is_voice = False

        # asr相关变量
        # 因为实际部署时可能会用到公共的本地ASR，不能把变量暴露给公共ASR
        # 所以涉及到ASR的变量，需要在这里定义，属于connection的私有变量
        self.asr_audio = []
        self.asr_audio_queue = queue.Queue()

        # llm相关变量
        self.llm_finish_task = True
        self.dialogue = Dialogue()

        # tts相关变量
        self.sentence_id = None
        # 处理TTS响应没有文本返回
        self.tts_MessageText = ""

        # iot相关变量
        self.iot_descriptors = {}
        self.func_handler = None

        self.cmd_exit = self.config["exit_commands"]
        self.max_cmd_length = 0
        for cmd in self.cmd_exit:
            if len(cmd) > self.max_cmd_length:
                self.max_cmd_length = len(cmd)

        # 是否在聊天结束后关闭连接
        self.close_after_chat = False
        self.load_function_plugin = False
        self.intent_type = "nointent"

        # careconnect: per-client Revel command prefix (ai_agent.bot_name)
        self.cc_agent_id = None
        self.cc_bot_name = None
        # Last authorized knowledge lookup for this turn (internal metadata only)
        self.cc_last_knowledge = None
        self.cc_last_revel_action = None

        self.timeout_seconds = (
            int(self.config.get("close_connection_no_voice_time", 120)) + 60
        )  # 在原来第一道关闭的基础上加60秒，进行二道关闭
        self.timeout_task = None

        # {"mcp":true} 表示启用MCP功能
        self.features = None

        # 初始化提示词管理器
        self.prompt_manager = PromptManager(config, self.logger)

    async def handle_connection(self, ws):
        try:
            # 获取并验证headers
            self.headers = dict(ws.request.headers)

            # careconnect: detect the xiaozhi-mqtt-gateway path. The gateway
            # connects with ?from=mqtt_gateway and frames every binary audio
            # message as [16-byte header][opus] (timestamp@8, opus_len@12,
            # opus@16) in BOTH directions. Raw opus (no header) garbles audio
            # on the real device ("cranky broken speaker") and yields empty ASR.
            # When this flag is set we strip the header on receive and add it
            # on send. Direct WS clients (emulator) keep raw opus.
            self.conn_from_mqtt_gateway = False
            try:
                from urllib.parse import urlparse, parse_qs as _pqs

                _rp = ws.request.path or ""
                self.conn_from_mqtt_gateway = (
                    _pqs(urlparse(_rp).query).get("from", [""])[0] == "mqtt_gateway"
                )
            except Exception:
                pass

            if self.headers.get("device-id", None) is None:
                # 尝试从 URL 的查询参数中获取 device-id
                from urllib.parse import parse_qs, urlparse

                # 从 WebSocket 请求中获取路径
                request_path = ws.request.path
                if not request_path:
                    self.logger.bind(tag=TAG).error("无法获取请求路径")
                    return
                parsed_url = urlparse(request_path)
                query_params = parse_qs(parsed_url.query)
                if "device-id" in query_params:
                    self.headers["device-id"] = query_params["device-id"][0]
                    self.headers["client-id"] = query_params["client-id"][0]
                else:
                    await ws.send("端口正常，如需测试连接，请使用test_page.html")
                    await self.close(ws)
                    return
            real_ip = self.headers.get("x-real-ip") or self.headers.get(
                "x-forwarded-for"
            )
            if real_ip:
                self.client_ip = real_ip.split(",")[0].strip()
            else:
                self.client_ip = ws.remote_address[0]
            self.logger.bind(tag=TAG).info(
                f"{self.client_ip} conn - Headers: {self.headers}"
            )

            # 进行认证
            await self.auth.authenticate(self.headers)

            # 认证通过,继续处理
            self.websocket = ws
            self.device_id = self.headers.get("device-id", None)

            # careconnect: register/update the Watcher in ai_device as soon as
            # Device-Id is accepted. W1-A firmware never POSTs /watcher/heartbeat,
            # so this is what makes the device appear on the Devices page.
            # Non-blocking: run in the connection executor so a DB hiccup
            # cannot stall the hello / MCP path.
            if self.device_id:
                self.executor.submit(self._cc_register_watcher)

            # 初始化活动时间戳
            self.last_activity_time = time.time() * 1000

            # 启动超时检查任务
            self.timeout_task = asyncio.create_task(self._check_timeout())

            # careconnect: per-connection reminder/medication delivery loop —
            # speaks due reminders while connected and replays any that came due
            # while the Watcher was in standby (it can't be woken remotely).
            self.cc_reminder_task = asyncio.create_task(self._cc_reminder_loop())

            self.welcome_msg = self.config["xiaozhi"]
            self.welcome_msg["session_id"] = self.session_id

            # 获取差异化配置
            self._initialize_private_config()
            # 异步初始化
            self.executor.submit(self._initialize_components)

            try:
                async for message in self.websocket:
                    await self._route_message(message)
            except websockets.exceptions.ConnectionClosed:
                self.logger.bind(tag=TAG).info("客户端断开连接")

        except AuthenticationError as e:
            self.logger.bind(tag=TAG).error(f"Authentication failed: {str(e)}")
            return
        except Exception as e:
            stack_trace = traceback.format_exc()
            self.logger.bind(tag=TAG).error(f"Connection error: {str(e)}-{stack_trace}")
            return
        finally:
            try:
                await self._save_and_close(ws)
            except Exception as final_error:
                self.logger.bind(tag=TAG).error(f"最终清理时出错: {final_error}")
                # 确保即使保存记忆失败，也要关闭连接
                try:
                    await self.close(ws)
                except Exception as close_error:
                    self.logger.bind(tag=TAG).error(
                        f"强制关闭连接时出错: {close_error}"
                    )

    async def _save_and_close(self, ws):
        """保存记忆并关闭连接"""
        try:
            if self.memory:
                # 使用线程池异步保存记忆
                def save_memory_task():
                    try:
                        # 创建新事件循环（避免与主循环冲突）
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self.memory.save_memory(self.dialogue.dialogue)
                        )
                    except Exception as e:
                        self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
                    finally:
                        try:
                            loop.close()
                        except Exception:
                            pass

                # 启动线程保存记忆，不等待完成
                threading.Thread(target=save_memory_task, daemon=True).start()
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
        finally:
            # 立即关闭连接，不等待记忆保存完成
            try:
                await self.close(ws)
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(
                    f"保存记忆后关闭连接失败: {close_error}"
                )

    async def _route_message(self, message):
        """消息路由"""
        if isinstance(message, str):
            self.last_activity_time = time.time() * 1000
            await handleTextMessage(self, message)
        elif isinstance(message, bytes):
            if self.vad is None:
                return
            if self.asr is None:
                return
            # careconnect: strip the 16-byte gateway frame header to recover the
            # raw opus the ASR pipeline expects (see conn_from_mqtt_gateway).
            if getattr(self, "conn_from_mqtt_gateway", False) and len(message) >= 16:
                opus_len = int.from_bytes(message[12:16], "big")
                message = message[16 : 16 + opus_len]
            self.asr_audio_queue.put(message)

    async def handle_restart(self, message):
        """处理服务器重启请求"""
        try:

            self.logger.bind(tag=TAG).info("收到服务器重启指令，准备执行...")

            # 发送确认响应
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "success",
                        "message": "服务器重启中...",
                        "content": {"action": "restart"},
                    }
                )
            )

            # 异步执行重启操作
            def restart_server():
                """实际执行重启的方法"""
                time.sleep(1)
                self.logger.bind(tag=TAG).info("执行服务器重启...")
                subprocess.Popen(
                    [sys.executable, "app.py"],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    start_new_session=True,
                )
                os._exit(0)

            # 使用线程执行重启避免阻塞事件循环
            threading.Thread(target=restart_server, daemon=True).start()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"重启失败: {str(e)}")
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": f"Restart failed: {str(e)}",
                        "content": {"action": "restart"},
                    }
                )
            )

    def _cc_register_watcher(self):
        """Ensure ai_device has a W1-A row for this connection's Device-Id.

        Called from the connection thread pool after auth. Must never raise
        into the WS loop — a missing/failed upsert only means the Devices
        page stays empty until the next successful connect.
        """
        try:
            from config.careconnect_db import ensure_watcher_device

            ensure_watcher_device(self.device_id or "")
        except Exception as e:
            self.logger.bind(tag=TAG).warning(
                f"careconnect watcher register failed (non-fatal): {e}"
            )

    def _initialize_components(self):
        try:
            self.selected_module_str = build_module_string(
                self.config.get("selected_module", {})
            )
            self.logger = create_connection_logger(self.selected_module_str)

            """初始化组件"""
            if self.config.get("prompt") is not None:
                user_prompt = self.config["prompt"]
                # careconnect: per-agent persona override. If the device's MAC
                # is bound to an ai_agent row in our DB, use that agent's
                # system_prompt instead of the global one. Falls back silently
                # to the global prompt on any error so we never block startup.
                try:
                    from config.careconnect_db import lookup_agent_persona
                    persona = lookup_agent_persona(self.device_id or "")
                    if persona:
                        self.cc_agent_id = persona.get("agent_id")
                        self.cc_bot_name = (persona.get("bot_name") or "").strip() or None
                    if persona and persona.get("system_prompt"):
                        per_agent = persona["system_prompt"].strip()
                        first = persona.get("first_name")
                        # careconnect: pin the assistant's own identity as "Haizel"
                        # and make crystal-clear that the patient is a DIFFERENT
                        # person — otherwise the model sometimes introduces itself
                        # as the patient, which is wrong.
                        identity = (
                            "\n\n# WHO YOU ARE — HARD RULE (overrides everything above)\n"
                            "Your name is Haizel. You are a warm AI companion who looks after "
                            "this person. You are NOT them.\n"
                        )
                        if first:
                            identity += (
                                f"The person you are talking to and caring for is named {first}. "
                                f"Address them as {first}. "
                                f"NEVER say \"I am {first}\" and NEVER introduce yourself as {first} — "
                                f"that is THEIR name, not yours.\n"
                            )
                        identity += "If anyone asks your name, say you are Haizel."
                        user_prompt = per_agent + identity
                        self.config["prompt"] = user_prompt  # also flow through to RAG enhancement
                        self.logger.bind(tag=TAG).info(
                            f"careconnect persona override: agent={persona['agent_id'][:8]} "
                            f"name={persona.get('agent_name')!r} "
                            f"prompt_len={len(user_prompt)}"
                        )
                    else:
                        self.logger.bind(tag=TAG).info(
                            f"careconnect persona: no agent bound for {self.device_id}, "
                            f"using global prompt"
                        )
                except Exception as e:
                    self.logger.bind(tag=TAG).warning(
                        f"careconnect persona lookup failed (non-fatal): {e}"
                    )
                # 使用快速提示词进行初始化
                prompt = self.prompt_manager.get_quick_prompt(user_prompt)
                self.change_system_prompt(prompt)
                self.logger.bind(tag=TAG).info(
                    f"快速初始化组件: prompt成功 {prompt[:50]}..."
                )

            """初始化本地组件"""
            if self.vad is None:
                self.vad = self._vad
            if self.asr is None:
                self.asr = self._initialize_asr()

            # 初始化声纹识别
            self._initialize_voiceprint()

            # 打开语音识别通道
            asyncio.run_coroutine_threadsafe(
                self.asr.open_audio_channels(self), self.loop
            )
            if self.tts is None:
                self.tts = self._initialize_tts()
            # careconnect: apply this device's selected voice + speed (from
            # data/voice_config.json, keyed by MAC) to the CustomTTS params so
            # the multi-engine TTS server renders the right voice. Falls back to
            # the file's default; safe no-op for providers without `params`.
            try:
                from config.voice_config import get_voice as _cc_get_voice

                _vc = _cc_get_voice(self.device_id or "")
                # per-device response verbosity (brief|normal|detailed) — drives
                # both the prompt directive and the Ollama num_predict token cap.
                self.cc_response_length = _vc.get("response_length", "brief")
                if hasattr(self.tts, "params") and isinstance(self.tts.params, dict):
                    self.tts.params["voice"] = _vc.get("voice", "kokoro:af_heart")
                    self.tts.params["speed"] = _vc.get("speed", "normal")
                    self.logger.bind(tag=TAG).info(
                        f"careconnect voice for {self.device_id}: "
                        f"{self.tts.params['voice']} ({self.tts.params['speed']}) "
                        f"length={self.cc_response_length}"
                    )
            except Exception as _e:
                self.cc_response_length = "brief"
                self.logger.bind(tag=TAG).warning(f"voice_config apply failed: {_e}")
            # 打开语音合成通道
            asyncio.run_coroutine_threadsafe(
                self.tts.open_audio_channels(self), self.loop
            )

            """加载记忆"""
            self._initialize_memory()
            """加载意图识别"""
            self._initialize_intent()
            """初始化上报线程"""
            self._init_report_threads()
            """更新系统提示词"""
            self._init_prompt_enhancement()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"实例化组件失败: {e}")

    def _init_prompt_enhancement(self):
        # 更新上下文信息
        self.prompt_manager.update_context_info(self, self.client_ip)
        enhanced_prompt = self.prompt_manager.build_enhanced_prompt(
            self.config["prompt"], self.device_id, self.client_ip,
            response_length=getattr(self, "cc_response_length", "brief"),
        )
        if enhanced_prompt:
            # Inject local-vector RAG context (best-effort, non-blocking on failure)
            rag_block = self._fetch_rag_context()
            if rag_block:
                enhanced_prompt = (
                    enhanced_prompt.rstrip()
                    + "\n\n## Past context for this patient\n"
                    + rag_block
                )
                self.logger.bind(tag=TAG).info(
                    f"RAG context injected: {len(rag_block)} chars, "
                    f"new prompt len={len(enhanced_prompt)}"
                )
            else:
                self.logger.bind(tag=TAG).info("RAG context: empty")
            self.change_system_prompt(enhanced_prompt)
            self.logger.bind(tag=TAG).info("系统提示词已增强更新")

    def _fetch_rag_context(self) -> str:
        """Query the memory provider for per-device past snippets.

        Best-effort: any failure or timeout returns an empty string so the
        chat path is never blocked by the RAG layer.
        """
        try:
            mem = getattr(self, "memory", None)
            if mem is None:
                return ""
            if not hasattr(mem, "query_memory"):
                return ""
            # Pick the best query string available at connection time.
            query_text = ""
            try:
                for m in reversed(getattr(self.dialogue, "dialogue", []) or []):
                    role = getattr(m, "role", None)
                    content = (getattr(m, "content", None) or "").strip()
                    if role == "user" and content:
                        query_text = content
                        break
            except Exception:
                query_text = ""
            if not query_text:
                query_text = "what should I know about this patient"

            import asyncio as _asyncio

            async def _runner():
                return await mem.query_memory(query_text)

            try:
                return _asyncio.run(
                    _asyncio.wait_for(_runner(), timeout=3.0)
                ) or ""
            except RuntimeError:
                # already in an event loop; fall back to sync schedule
                fut = _asyncio.run_coroutine_threadsafe(_runner(), self.loop)
                return fut.result(timeout=3.0) or ""
        except Exception as e:
            try:
                self.logger.bind(tag=TAG).warning(f"RAG context fetch failed: {e}")
            except Exception:
                pass
            return ""

    def _init_report_threads(self):
        """初始化ASR和TTS上报线程

        careconnect override: upstream gates this on ``read_config_from_api``
        (manager-api connection) but our reportHandle.report() now writes
        directly to MariaDB regardless. Always start the worker so chat
        history lands in the dashboard.
        """
        if self.report_thread is None or not self.report_thread.is_alive():
            self.report_thread = threading.Thread(
                target=self._report_worker, daemon=True
            )
            self.report_thread.start()
            self.logger.bind(tag=TAG).info("TTS上报线程已启动 (careconnect always-on)")

    def _initialize_tts(self):
        """初始化TTS"""
        tts = None
        if not self.need_bind:
            tts = initialize_tts(self.config)

        if tts is None:
            tts = DefaultTTS(self.config, delete_audio_file=True)

        return tts

    def _initialize_asr(self):
        """初始化ASR"""
        if self._asr.interface_type == InterfaceType.LOCAL:
            # 如果公共ASR是本地服务，则直接返回
            # 因为本地一个实例ASR，可以被多个连接共享
            asr = self._asr
        else:
            # 如果公共ASR是远程服务，则初始化一个新实例
            # 因为远程ASR，涉及到websocket连接和接收线程，需要每个连接一个实例
            asr = initialize_asr(self.config)

        return asr

    def _initialize_voiceprint(self):
        """为当前连接初始化声纹识别"""
        try:
            voiceprint_config = self.config.get("voiceprint", {})
            if voiceprint_config:
                self.voiceprint_provider = VoiceprintProvider(voiceprint_config)
                self.logger.bind(tag=TAG).info("声纹识别功能已在连接时动态启用")
            else:
                self.logger.bind(tag=TAG).info("声纹识别功能未启用或配置不完整")
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"声纹识别初始化失败: {str(e)}")

    def _initialize_private_config(self):
        """如果是从配置文件获取，则进行二次实例化"""
        if not self.read_config_from_api:
            return
        """从接口获取差异化的配置进行二次实例化，非全量重新实例化"""
        try:
            begin_time = time.time()
            private_config = get_private_config_from_api(
                self.config,
                self.headers.get("device-id"),
                self.headers.get("client-id", self.headers.get("device-id")),
            )
            private_config["delete_audio"] = bool(self.config.get("delete_audio", True))
            self.logger.bind(tag=TAG).info(
                f"{time.time() - begin_time} 秒，获取差异化配置成功: {json.dumps(filter_sensitive_info(private_config), ensure_ascii=False)}"
            )
        except DeviceNotFoundException as e:
            self.need_bind = True
            private_config = {}
        except DeviceBindException as e:
            self.need_bind = True
            self.bind_code = e.bind_code
            private_config = {}
        except Exception as e:
            self.need_bind = True
            self.logger.bind(tag=TAG).error(f"获取差异化配置失败: {e}")
            private_config = {}

        init_llm, init_tts, init_memory, init_intent = (
            False,
            False,
            False,
            False,
        )

        init_vad = check_vad_update(self.common_config, private_config)
        init_asr = check_asr_update(self.common_config, private_config)

        if init_vad:
            self.config["VAD"] = private_config["VAD"]
            self.config["selected_module"]["VAD"] = private_config["selected_module"][
                "VAD"
            ]
        if init_asr:
            self.config["ASR"] = private_config["ASR"]
            self.config["selected_module"]["ASR"] = private_config["selected_module"][
                "ASR"
            ]
        if private_config.get("TTS", None) is not None:
            init_tts = True
            self.config["TTS"] = private_config["TTS"]
            self.config["selected_module"]["TTS"] = private_config["selected_module"][
                "TTS"
            ]
        if private_config.get("LLM", None) is not None:
            init_llm = True
            self.config["LLM"] = private_config["LLM"]
            self.config["selected_module"]["LLM"] = private_config["selected_module"][
                "LLM"
            ]
        if private_config.get("VLLM", None) is not None:
            self.config["VLLM"] = private_config["VLLM"]
            self.config["selected_module"]["VLLM"] = private_config["selected_module"][
                "VLLM"
            ]
        if private_config.get("Memory", None) is not None:
            init_memory = True
            self.config["Memory"] = private_config["Memory"]
            self.config["selected_module"]["Memory"] = private_config[
                "selected_module"
            ]["Memory"]
        if private_config.get("Intent", None) is not None:
            init_intent = True
            self.config["Intent"] = private_config["Intent"]
            model_intent = private_config.get("selected_module", {}).get("Intent", {})
            self.config["selected_module"]["Intent"] = model_intent
            # 加载插件配置
            if model_intent != "Intent_nointent":
                plugin_from_server = private_config.get("plugins", {})
                for plugin, config_str in plugin_from_server.items():
                    plugin_from_server[plugin] = json.loads(config_str)
                self.config["plugins"] = plugin_from_server
                self.config["Intent"][self.config["selected_module"]["Intent"]][
                    "functions"
                ] = plugin_from_server.keys()
        if private_config.get("prompt", None) is not None:
            self.config["prompt"] = private_config["prompt"]
        # 获取声纹信息
        if private_config.get("voiceprint", None) is not None:
            self.config["voiceprint"] = private_config["voiceprint"]
        if private_config.get("summaryMemory", None) is not None:
            self.config["summaryMemory"] = private_config["summaryMemory"]
        if private_config.get("device_max_output_size", None) is not None:
            self.max_output_size = int(private_config["device_max_output_size"])
        if private_config.get("chat_history_conf", None) is not None:
            self.chat_history_conf = int(private_config["chat_history_conf"])
        if private_config.get("mcp_endpoint", None) is not None:
            self.config["mcp_endpoint"] = private_config["mcp_endpoint"]
        try:
            modules = initialize_modules(
                self.logger,
                private_config,
                init_vad,
                init_asr,
                init_llm,
                init_tts,
                init_memory,
                init_intent,
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"初始化组件失败: {e}")
            modules = {}
        if modules.get("tts", None) is not None:
            self.tts = modules["tts"]
        if modules.get("vad", None) is not None:
            self.vad = modules["vad"]
        if modules.get("asr", None) is not None:
            self.asr = modules["asr"]
        if modules.get("llm", None) is not None:
            self.llm = modules["llm"]
        if modules.get("intent", None) is not None:
            self.intent = modules["intent"]
        if modules.get("memory", None) is not None:
            self.memory = modules["memory"]

    def _initialize_memory(self):
        if self.memory is None:
            return
        """初始化记忆模块"""
        self.memory.init_memory(
            role_id=self.device_id,
            llm=self.llm,
            summary_memory=self.config.get("summaryMemory", None),
            save_to_file=not self.read_config_from_api,
        )

        # 获取记忆总结配置
        memory_config = self.config["Memory"]
        memory_type = self.config["Memory"][self.config["selected_module"]["Memory"]][
            "type"
        ]
        # 如果使用 nomen，直接返回
        if memory_type == "nomem":
            return
        # 使用 mem_local_vector 模式 (RAG over per-device dialogue snippets)
        elif memory_type == "mem_local_vector":
            # mem_local_vector does not need a summarization LLM, but keep main
            # LLM available for any future hybrid behavior.
            self.memory.set_llm(self.llm)
            return
        # 使用 mem_local_short 模式
        elif memory_type == "mem_local_short":
            memory_llm_name = memory_config[self.config["selected_module"]["Memory"]][
                "llm"
            ]
            if memory_llm_name and memory_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                memory_llm_config = self.config["LLM"][memory_llm_name]
                memory_llm_type = memory_llm_config.get("type", memory_llm_name)
                memory_llm = llm_utils.create_instance(
                    memory_llm_type, memory_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为记忆总结创建了专用LLM: {memory_llm_name}, 类型: {memory_llm_type}"
                )
                self.memory.set_llm(memory_llm)
            else:
                # 否则使用主LLM
                self.memory.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

    def _initialize_intent(self):
        if self.intent is None:
            return
        self.intent_type = self.config["Intent"][
            self.config["selected_module"]["Intent"]
        ]["type"]
        if self.intent_type == "function_call" or self.intent_type == "intent_llm":
            self.load_function_plugin = True
        """初始化意图识别模块"""
        # 获取意图识别配置
        intent_config = self.config["Intent"]
        intent_type = self.config["Intent"][self.config["selected_module"]["Intent"]][
            "type"
        ]

        # 如果使用 nointent，直接返回
        if intent_type == "nointent":
            return
        # 使用 intent_llm 模式
        elif intent_type == "intent_llm":
            intent_llm_name = intent_config[self.config["selected_module"]["Intent"]][
                "llm"
            ]

            if intent_llm_name and intent_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                intent_llm_config = self.config["LLM"][intent_llm_name]
                intent_llm_type = intent_llm_config.get("type", intent_llm_name)
                intent_llm = llm_utils.create_instance(
                    intent_llm_type, intent_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为意图识别创建了专用LLM: {intent_llm_name}, 类型: {intent_llm_type}"
                )
                self.intent.set_llm(intent_llm)
            else:
                # 否则使用主LLM
                self.intent.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

        """加载统一工具处理器"""
        self.func_handler = UnifiedToolHandler(self)

        # 异步初始化工具处理器
        if hasattr(self, "loop") and self.loop:
            asyncio.run_coroutine_threadsafe(self.func_handler._initialize(), self.loop)

    def change_system_prompt(self, prompt):
        self.prompt = prompt
        # 更新系统prompt至上下文
        self.dialogue.update_system_message(self.prompt)

    # careconnect: phrases that mean "use the camera". Strict-ish so it doesn't
    # hijack ordinary conversation; if it ever misfires, capture falls through.
    _CC_CAMERA_RE = re.compile(
        r"\b(take|takes|taking|snap|capture|grab)\b[^.?!]*\b(photo|picture|pic|snapshot|image|shot)\b"
        r"|\bwhat\s+(do|can|am)\s+(you|i)\s+(see|seeing|looking)\b"
        r"|\bwhat\s+is\s+this\b|\bwhat\s+am\s+i\s+(holding|looking at)\b"
        r"|\bdescribe\s+(what|this|the|it)\b|\blook\s+at\s+(this|that|it)\b"
        r"|\bcan\s+you\s+see\b",
        re.IGNORECASE,
    )

    def _cc_is_camera_request(self, query: str) -> bool:
        try:
            return bool(query and self._CC_CAMERA_RE.search(query))
        except Exception:
            return False

    def _cc_prior_user_text(self, current: str) -> str | None:
        current_stripped = (current or "").strip()
        prior = None
        try:
            for msg in getattr(self.dialogue, "dialogue", []) or []:
                if getattr(msg, "role", None) != "user":
                    continue
                text = (getattr(msg, "content", None) or "").strip()
                if text and text != current_stripped:
                    prior = text
        except Exception:
            return None
        return prior

    def _cc_ground_llm_messages(self, query: str, messages: list) -> list:
        """Append authorized Nexus Knowledge to this LLM call only. Fail-open.

        Never mutates the stored persona / system prompt. Does not execute Revel.
        """
        original = messages
        try:
            from core.knowledge_grounding import env_int, ground_turn_messages, knowledge_enabled
            from config.careconnect_db import search_device_knowledge

            grounded, meta = ground_turn_messages(
                mac=self.device_id or "",
                query=query,
                messages=messages,
                search=search_device_knowledge,
                enabled=knowledge_enabled(),
                max_results=env_int("CC_KNOWLEDGE_CONTEXT_MAX_RESULTS", 3),
                max_chars=env_int("CC_KNOWLEDGE_CONTEXT_MAX_CHARS", 6000),
                recent_user=self._cc_prior_user_text(query),
                logger=self.logger.bind(tag=TAG),
            )
            self.cc_last_knowledge = meta
            return grounded
        except Exception as exc:
            self.cc_last_knowledge = {
                "grounding_applied": False,
                "skipped": "error",
                "revel_execute": False,
            }
            try:
                self.logger.bind(tag=TAG).warning(
                    f"knowledge grounding failed (non-fatal): {exc}"
                )
            except Exception:
                pass
            return original

    def _cc_maybe_knowledge_revel(self, query: str) -> None:
        """Structured Knowledge Revel decision. Fail-open. Does not scan LLM text."""
        try:
            from core.knowledge_grounding import (
                knowledge_revel_enabled,
                request_knowledge_revel,
                should_request_knowledge_revel,
            )
            from config.careconnect_db import post_knowledge_revel

            meta = getattr(self, "cc_last_knowledge", None) or {}
            if not should_request_knowledge_revel(
                meta, revel_enabled=knowledge_revel_enabled()
            ):
                return
            already = None
            last = getattr(self, "cc_last_revel_action", None) or {}
            if last.get("source") == "keyword" and last.get("tag"):
                already = last.get("tag")
            decision = request_knowledge_revel(
                mac=self.device_id or "",
                query=query,
                already_executed_tag=already,
                decide=post_knowledge_revel,
                logger=self.logger.bind(tag=TAG),
            )
            self.cc_last_revel_action = {
                "tag": (decision or {}).get("revelTag"),
                "source": "knowledge",
                "executed": bool((decision or {}).get("executed")),
                "reason": (decision or {}).get("reason"),
                "topicId": (decision or {}).get("topicId"),
                "score": (decision or {}).get("score"),
            }
        except Exception as exc:
            try:
                self.logger.bind(tag=TAG).warning(
                    f"knowledge revel failed (non-fatal): {exc}"
                )
            except Exception:
                pass

    # careconnect: phrases that mean "end the conversation / go back to sleep".
    # The device runs in auto-listen mode, so without this it keeps the mic open
    # after every reply ("always listening"). With Intent: nointent the
    # function_call exit plugin never fires, so we detect goodbye here and set
    # close_after_chat — the LLM speaks a short farewell, then the session closes
    # and the Watcher returns to standby (say "Jarvis" to wake it again).
    _CC_GOODBYE_RE = re.compile(
        r"^\s*(?:ok(?:ay)?|alright|well|thanks?|thank you)?[\s,]*"
        r"(?:good\s?bye|goodbye|bye(?:\s?bye)?|good\s?night|goodnight|"
        r"see you(?:\s+later)?|talk(?:\s+to\s+you)?\s+later|"
        r"that(?:'?s| is| will be)?\s+all|that's it|i'?m\s+done|we'?re\s+done|"
        r"no(?:thing)?\s+(?:that's all|else|more)|"
        r"go\s+to\s+sleep|you\s+can\s+(?:go|rest)(?:\s+now)?|"
        r"stop\s+listening|that'?ll\s+be\s+all)"
        r"[\s.!,]*$",
        re.IGNORECASE,
    )

    def _cc_is_goodbye(self, query: str) -> bool:
        try:
            return bool(query and self._CC_GOODBYE_RE.search(query.strip()))
        except Exception:
            return False

    # careconnect: speak a standalone sentence to the device right now, without
    # an LLM round-trip, framed like a normal reply so the Watcher plays the
    # audio. Used for reminder confirmations + proactive reminder delivery.
    def _cc_speak_now(self, text: str, log_turn: bool = True) -> None:
        try:
            if not text or self.tts is None:
                return
            self.llm_finish_task = False
            self.client_abort = False
            self.sentence_id = uuid.uuid4().hex
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=self.sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )
            self.tts.tts_one_sentence(self, ContentType.TEXT, content_detail=text)
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=self.sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
            self.llm_finish_task = True
            if log_turn:
                try:
                    from config.careconnect_db import report as _cc_report

                    _cc_report(self.device_id, getattr(self, "session_id", "") or "", 2, text)
                except Exception:
                    pass
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"careconnect speak_now failed: {e}")

    async def _cc_reminder_loop(self):
        """Per-connection medication/reminder delivery. Every ~20s, speak any
        reminder that has come due for this device's patient; overdue ones that
        fired while the Watcher was in standby are replayed on connect with a
        gentle 'while you were away' lead. Daily reminders roll to the next day.
        """
        from config import reminders as _cc_rem
        from config.careconnect_db import lookup_agent_id as _lk

        try:
            agent_id = _lk(self.device_id or "")
        except Exception:
            agent_id = None
        if not agent_id:
            return

        first_pass = True
        try:
            while not self.stop_event.is_set():
                try:
                    for r in _cc_rem.due_reminders(agent_id):
                        # wait out any active turn so we don't talk over the user
                        waited = 0
                        while self.client_is_speaking and waited < 30 and not self.stop_event.is_set():
                            await asyncio.sleep(1)
                            waited += 1
                        if self.stop_event.is_set():
                            return
                        task = r.get("text") or "your reminder"
                        overdue = int(r.get("due_at", 0)) < time.time() - 60
                        lead = "While you were away, " if (first_pass and overdue) else ""
                        if task == "your reminder":
                            body = f"{lead}here is your reminder." if lead else "Here is your reminder."
                        else:
                            body = (f"{lead}this is your reminder to {task}." if lead
                                    else f"This is your reminder to {task}.")
                        self._cc_speak_now(body[0].upper() + body[1:])
                        _cc_rem.mark_fired(agent_id, r.get("id"))
                        await asyncio.sleep(2)
                    first_pass = False
                except Exception as e:
                    self.logger.bind(tag=TAG).debug(f"careconnect reminder loop tick: {e}")
                await asyncio.sleep(20)
        except asyncio.CancelledError:
            pass

    def _cc_capture_and_describe(self, query: str):
        """Trigger the device camera + return the vision description, or None.

        Mirrors core/providers/tools/device_mcp/mcp_executor: call the device's
        self.camera.take_photo MCP tool with the user's question; the device
        uploads the frame to vision_explain and returns the description text.
        Everything is guarded so a failure degrades to a normal spoken reply.
        """
        try:
            from core.providers.tools.device_mcp.mcp_handler import call_mcp_tool

            mcp_client = getattr(self, "mcp_client", None)
            if mcp_client is None:
                return None
            tool = None
            for cand in ("self.camera.take_photo", "self_camera_take_photo"):
                if mcp_client.has_tool(cand):
                    tool = cand
                    break
            if tool is None:
                self.logger.bind(tag=TAG).info(
                    "careconnect camera: device has no take_photo tool; skipping"
                )
                return None
            args = json.dumps({"question": query or "What is in this image?"})
            # The Watcher camera intermittently returns {"success": false,
            # "message": "Failed to capture photo"} (a device-side hardware/firmware
            # hiccup, often on the first try right after a reconnect). Retry a few
            # times with a short warm-up delay before giving up.
            last_msg = None
            for attempt in range(3):
                try:
                    fut = asyncio.run_coroutine_threadsafe(
                        call_mcp_tool(self, mcp_client, tool, args, timeout=30),
                        self.loop,
                    )
                    result = fut.result(timeout=35)
                except Exception as e:
                    last_msg = str(e)
                    self.logger.bind(tag=TAG).warning(
                        f"careconnect camera: attempt {attempt + 1} errored: {e}"
                    )
                    time.sleep(1.2)
                    continue
                # MCP tool result may be a plain string or {"content":[{"text":...}]}
                text = ""
                if isinstance(result, str):
                    text = result
                elif isinstance(result, dict):
                    content = result.get("content")
                    if isinstance(content, list):
                        text = " ".join(
                            c.get("text", "") for c in content if isinstance(c, dict)
                        ).strip()
                    else:
                        text = str(result.get("text") or content or "")
                text = (text or "").strip()
                # Parse the device/vision JSON ({"success","response","photo_url",...})
                description, photo_url, ok = text, None, True
                inner = None
                got_response = False
                try:
                    inner = json.loads(text)
                    if isinstance(inner, dict):
                        if inner.get("success") is False:
                            ok = False
                            last_msg = inner.get("message") or "capture failed"
                        resp = inner.get("response")
                        if resp:
                            description = str(resp).strip()
                            got_response = True
                        photo_url = inner.get("photo_url")
                except Exception:
                    got_response = bool(description)
                if not ok or not got_response:
                    self.logger.bind(tag=TAG).info(
                        f"careconnect camera: attempt {attempt + 1} no-photo ({last_msg})"
                    )
                    time.sleep(1.2)
                    continue
                self.logger.bind(tag=TAG).info(
                    f"careconnect camera: described image -> {description[:120]} (photo={photo_url})"
                )
                return {"ok": True, "description": description, "photo_url": photo_url}
            self.logger.bind(tag=TAG).warning(
                f"careconnect camera: capture failed after retries ({last_msg})"
            )
            return {"ok": False}
        except Exception as e:
            self.logger.bind(tag=TAG).warning(
                f"careconnect camera capture failed (non-fatal): {e}"
            )
            return {"ok": False}

    def _cc_post_photo_turn(self, photo_url, description):
        """Log a client-side chat turn carrying the captured photo so it shows in
        the dashboard conversation (rendered from the [[photo:url]] marker)."""
        if not photo_url:
            return
        try:
            from config.careconnect_db import report as _cc_report

            content = f"[[photo:{photo_url}]] {description}"
            _cc_report(self.device_id, getattr(self, "session_id", "") or "", 1, content)
            self.logger.bind(tag=TAG).info(
                f"careconnect camera: posted photo turn {photo_url}"
            )
        except Exception as e:
            self.logger.bind(tag=TAG).warning(
                f"careconnect post photo turn failed (non-fatal): {e}"
            )

    def chat(self, query, tool_call=False, depth=0):
        self.logger.bind(tag=TAG).info(f"大模型收到用户消息: {query}")
        self.llm_finish_task = False

        # careconnect fix: components are initialized asynchronously via
        # executor.submit(self._initialize_components). On the first turn the
        # chat task can win the race against that init and reach the FIRST-TTS
        # put below while self.tts (and self.llm) are still None — raising an
        # AttributeError that is silently swallowed by the unawaited executor
        # Future (no LLM reply, no audio, no logged traceback). Wait briefly
        # for the async init to finish before proceeding.
        if self.tts is None or self.llm is None:
            _wait_deadline = time.time() + 10.0
            while (self.tts is None or self.llm is None) and time.time() < _wait_deadline:
                time.sleep(0.05)
            if self.tts is None or self.llm is None:
                self.logger.bind(tag=TAG).error(
                    "组件未就绪，放弃本轮对话 (tts=%s llm=%s)"
                    % (self.tts is not None, self.llm is not None)
                )
                return None

        # careconnect: keyword-triggered camera. llama3.1 with nointent will not
        # emit the self.camera.take_photo MCP call (it just *says* it took one).
        # Detect the request now; the actual capture runs AFTER the FIRST marker
        # so we can speak an instant ack during the (~20-30s) capture+vision.
        _cc_camera = (
            not tool_call and depth == 0 and self._cc_is_camera_request(query)
        )

        # careconnect: end-of-conversation. If the person said goodbye, let the
        # LLM speak a short farewell, then close the session so the device stops
        # listening and returns to standby (re-wake by saying "Jarvis").
        if not tool_call and depth == 0 and not _cc_camera and self._cc_is_goodbye(query):
            self.close_after_chat = True
            self.logger.bind(tag=TAG).info(
                f"careconnect goodbye detected ({query!r}) -> closing after farewell"
            )

        # careconnect: capture a spoken reminder ("remind me to ... at ...").
        # Handled deterministically (no LLM) so the time is parsed reliably and
        # a per-connection loop can speak it when due.
        if not tool_call and depth == 0 and not _cc_camera and not self.close_after_chat:
            try:
                from config import reminders as _cc_rem

                if _cc_rem.is_reminder_request(query):
                    self.dialogue.put(Message(role="user", content=query))
                    agent_id = None
                    try:
                        from config.careconnect_db import lookup_agent_id as _lk
                        agent_id = _lk(self.device_id or "")
                    except Exception:
                        agent_id = None
                    parsed = _cc_rem.parse_reminder(query) if agent_id else None
                    if agent_id and parsed:
                        _cc_rem.add_reminder(
                            agent_id, parsed["text"], parsed["due_at"], parsed["recurring"]
                        )
                        when = _cc_rem.humanize(parsed["due_at"])
                        phrase = when if when.startswith("in ") else f"at {when}"
                        every = "every day " if parsed["recurring"] == "daily" else ""
                        if parsed["text"] == "your reminder":
                            msg = f"Okay, I'll remind you {every}{phrase}."
                        else:
                            msg = f"Okay, I'll remind you to {parsed['text']} {every}{phrase}."
                        self.logger.bind(tag=TAG).info(
                            f"careconnect reminder set: agent={agent_id[:8]} "
                            f"text={parsed['text']!r} due={parsed['due_at']} rec={parsed['recurring']}"
                        )
                        self._cc_speak_now(msg)
                    elif agent_id:
                        self._cc_speak_now(
                            "I can set that reminder for you. When would you like me to remind you?"
                        )
                    else:
                        self._cc_speak_now(
                            "I'd be glad to remind you, but this device isn't linked to a profile yet."
                        )
                    return None
            except Exception as e:
                self.logger.bind(tag=TAG).warning(f"careconnect reminder capture failed: {e}")

        if not tool_call:
            self.dialogue.put(Message(role="user", content=query))

        # 为最顶层时新建会话ID和发送FIRST请求
        if depth == 0:
            self.sentence_id = str(uuid.uuid4().hex)
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=self.sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )

        # careconnect camera: speak an immediate ack (so there's no long silence),
        # then capture + describe, post the photo into the dashboard conversation,
        # and hand the description to the LLM so the spoken reply is natural.
        if _cc_camera:
            try:
                self.tts.tts_one_sentence(
                    self, ContentType.TEXT, content_detail="Okay, let me take a look."
                )
            except Exception:
                pass
            _shot = self._cc_capture_and_describe(query)
            if _shot and _shot.get("ok") and _shot.get("description"):
                self._cc_post_photo_turn(_shot.get("photo_url"), _shot["description"])
                self.dialogue.put(
                    Message(
                        role="user",
                        content=(
                            "[The camera just took a photo for the person. It shows: "
                            f"{_shot['description']}. In one or two warm, plain "
                            "sentences, tell them what you see.]"
                        ),
                    )
                )
            else:
                # device camera couldn't capture — apologize gently instead of
                # narrating the raw error JSON.
                self.dialogue.put(
                    Message(
                        role="user",
                        content=(
                            "[The camera could not take a photo just now. In one "
                            "short, warm sentence, gently let them know and suggest "
                            "trying again in a moment.]"
                        ),
                    )
                )

        # Define intent functions
        functions = None
        if self.intent_type == "function_call" and hasattr(self, "func_handler"):
            functions = self.func_handler.get_functions()
        response_message = []

        try:
            # 使用带记忆的对话
            memory_str = None
            if self.memory is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self.memory.query_memory(query), self.loop
                )
                memory_str = future.result()

            # careconnect: cap generation length per the device's verbosity
            # setting (brief|normal|detailed). The Ollama provider maps
            # max_tokens -> num_predict; other providers honor max_tokens too.
            # brief is bounded by the 2-sentence cap in the stream loop; this is
            # just a safety backstop so a runaway generation still ends.
            _cc_max_tokens = {"brief": 160, "normal": 240, "detailed": 800}.get(
                getattr(self, "cc_response_length", "brief"), 160
            )
            llm_messages = self.dialogue.get_llm_dialogue_with_memory(
                memory_str, self.config.get("voiceprint", {})
            )
            if not tool_call and depth == 0 and not _cc_camera:
                llm_messages = self._cc_ground_llm_messages(query, llm_messages)
                self._cc_maybe_knowledge_revel(query)
            if self.intent_type == "function_call" and functions is not None:
                # 使用支持functions的streaming接口
                llm_responses = self.llm.response_with_functions(
                    self.session_id,
                    llm_messages,
                    functions=functions,
                    max_tokens=_cc_max_tokens,
                )
            else:
                llm_responses = self.llm.response(
                    self.session_id,
                    llm_messages,
                    max_tokens=_cc_max_tokens,
                )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")
            return None

        # 处理流式响应
        tool_call_flag = False
        function_name = None
        function_id = None
        function_arguments = ""
        content_arguments = ""
        self.client_abort = False
        emotion_flag = True
        for response in llm_responses:
            if self.client_abort:
                break
            if self.intent_type == "function_call" and functions is not None:
                content, tools_call = response
                if "content" in response:
                    content = response["content"]
                    tools_call = None
                if content is not None and len(content) > 0:
                    content_arguments += content

                if not tool_call_flag and content_arguments.startswith("<tool_call>"):
                    # print("content_arguments", content_arguments)
                    tool_call_flag = True

                if tools_call is not None and len(tools_call) > 0:
                    tool_call_flag = True
                    if tools_call[0].id is not None:
                        function_id = tools_call[0].id
                    if tools_call[0].function.name is not None:
                        function_name = tools_call[0].function.name
                    if tools_call[0].function.arguments is not None:
                        function_arguments += tools_call[0].function.arguments
            else:
                content = response

            # 在llm回复中获取情绪表情，一轮对话只在开头获取一次
            if emotion_flag and content is not None and content.strip():
                asyncio.run_coroutine_threadsafe(
                    textUtils.get_emotion(self, content),
                    self.loop,
                )
                emotion_flag = False

            if content is not None and len(content) > 0:
                if not tool_call_flag:
                    # careconnect: stop after N COMPLETE sentences so the spoken
                    # reply stays short and is never cut mid-word (llama3.1
                    # ignores the prompt's length rule, so enforce it here).
                    # brief -> 2 sentences, normal -> 3, detailed -> uncapped.
                    _cc_len = getattr(self, "cc_response_length", "brief")
                    _cc_cap = {"brief": 2, "normal": 3}.get(_cc_len, 0)
                    if _cc_cap:
                        _joined = "".join(response_message) + content
                        _enders = [m.end() for m in re.finditer(r"[.!?。！？]", _joined)]
                        if len(_enders) >= _cc_cap:
                            _already = len("".join(response_message))
                            _send = _joined[_already:_enders[_cc_cap - 1]]
                            if _send:
                                response_message.append(_send)
                                self.tts.tts_text_queue.put(
                                    TTSMessageDTO(
                                        sentence_id=self.sentence_id,
                                        sentence_type=SentenceType.MIDDLE,
                                        content_type=ContentType.TEXT,
                                        content_detail=_send,
                                    )
                                )
                            break
                    response_message.append(content)
                    self.tts.tts_text_queue.put(
                        TTSMessageDTO(
                            sentence_id=self.sentence_id,
                            sentence_type=SentenceType.MIDDLE,
                            content_type=ContentType.TEXT,
                            content_detail=content,
                        )
                    )
        # 处理function call
        if tool_call_flag:
            bHasError = False
            if function_id is None:
                a = extract_json_from_string(content_arguments)
                if a is not None:
                    try:
                        content_arguments_json = json.loads(a)
                        function_name = content_arguments_json["name"]
                        function_arguments = json.dumps(
                            content_arguments_json["arguments"], ensure_ascii=False
                        )
                        function_id = str(uuid.uuid4().hex)
                    except Exception as e:
                        bHasError = True
                        response_message.append(a)
                else:
                    bHasError = True
                    response_message.append(content_arguments)
                if bHasError:
                    self.logger.bind(tag=TAG).error(
                        f"function call error: {content_arguments}"
                    )
            if not bHasError:
                # 如需要大模型先处理一轮，添加相关处理后的日志情况
                if len(response_message) > 0:
                    text_buff = "".join(response_message)
                    self.tts_MessageText = text_buff
                    self.dialogue.put(Message(role="assistant", content=text_buff))
                response_message.clear()
                self.logger.bind(tag=TAG).debug(
                    f"function_name={function_name}, function_id={function_id}, function_arguments={function_arguments}"
                )
                function_call_data = {
                    "name": function_name,
                    "id": function_id,
                    "arguments": function_arguments,
                }

                # 使用统一工具处理器处理所有工具调用
                result = asyncio.run_coroutine_threadsafe(
                    self.func_handler.handle_llm_function_call(
                        self, function_call_data
                    ),
                    self.loop,
                ).result()
                self._handle_function_result(result, function_call_data, depth=depth)

        # 存储对话内容
        if len(response_message) > 0:
            text_buff = "".join(response_message)
            self.tts_MessageText = text_buff
            self.dialogue.put(Message(role="assistant", content=text_buff))
        if depth == 0:
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=self.sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
        self.llm_finish_task = True
        # 使用lambda延迟计算，只有在DEBUG级别时才执行get_llm_dialogue()
        self.logger.bind(tag=TAG).debug(
            lambda: json.dumps(
                self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False
            )
        )

        return True

    def _handle_function_result(self, result, function_call_data, depth):
        if result.action == Action.RESPONSE:  # 直接回复前端
            text = result.response
            self.tts.tts_one_sentence(self, ContentType.TEXT, content_detail=text)
            self.dialogue.put(Message(role="assistant", content=text))
        elif result.action == Action.REQLLM:  # 调用函数后再请求llm生成回复
            text = result.result
            if text is not None and len(text) > 0:
                function_id = function_call_data["id"]
                function_name = function_call_data["name"]
                function_arguments = function_call_data["arguments"]
                self.dialogue.put(
                    Message(
                        role="assistant",
                        tool_calls=[
                            {
                                "id": function_id,
                                "function": {
                                    "arguments": function_arguments,
                                    "name": function_name,
                                },
                                "type": "function",
                                "index": 0,
                            }
                        ],
                    )
                )

                self.dialogue.put(
                    Message(
                        role="tool",
                        tool_call_id=(
                            str(uuid.uuid4()) if function_id is None else function_id
                        ),
                        content=text,
                    )
                )
                self.chat(text, tool_call=True, depth=depth + 1)
        elif result.action == Action.NOTFOUND or result.action == Action.ERROR:
            text = result.response if result.response else result.result
            self.tts.tts_one_sentence(self, ContentType.TEXT, content_detail=text)
            self.dialogue.put(Message(role="assistant", content=text))
        else:
            pass

    def _report_worker(self):
        """聊天记录上报工作线程"""
        while not self.stop_event.is_set():
            try:
                # 从队列获取数据，设置超时以便定期检查停止事件
                item = self.report_queue.get(timeout=1)
                if item is None:  # 检测毒丸对象
                    break
                try:
                    # 检查线程池状态
                    if self.executor is None:
                        continue
                    # 提交任务到线程池
                    self.executor.submit(self._process_report, *item)
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"聊天记录上报线程异常: {e}")
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"聊天记录上报工作线程异常: {e}")

        self.logger.bind(tag=TAG).info("聊天记录上报线程已退出")

    def _process_report(self, type, text, audio_data, report_time):
        """处理上报任务"""
        try:
            # 执行上报（传入二进制数据）
            report(self, type, text, audio_data, report_time)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"上报处理异常: {e}")
        finally:
            # 标记任务完成
            self.report_queue.task_done()

    def clearSpeakStatus(self):
        self.client_is_speaking = False
        self.logger.bind(tag=TAG).debug(f"清除服务端讲话状态")

    async def close(self, ws=None):
        """资源清理方法"""
        try:
            # 取消超时任务
            if self.timeout_task and not self.timeout_task.done():
                self.timeout_task.cancel()
                try:
                    await self.timeout_task
                except asyncio.CancelledError:
                    pass
                self.timeout_task = None

            # 清理工具处理器资源
            if hasattr(self, "func_handler") and self.func_handler:
                try:
                    await self.func_handler.cleanup()
                except Exception as cleanup_error:
                    self.logger.bind(tag=TAG).error(
                        f"清理工具处理器时出错: {cleanup_error}"
                    )

            # 触发停止事件
            if self.stop_event:
                self.stop_event.set()

            # 清空任务队列
            self.clear_queues()

            # 关闭WebSocket连接
            try:
                if ws:
                    # 安全地检查WebSocket状态并关闭
                    try:
                        if hasattr(ws, "closed") and not ws.closed:
                            await ws.close()
                        elif hasattr(ws, "state") and ws.state.name != "CLOSED":
                            await ws.close()
                        else:
                            # 如果没有closed属性，直接尝试关闭
                            await ws.close()
                    except Exception:
                        # 如果关闭失败，忽略错误
                        pass
                elif self.websocket:
                    try:
                        if (
                            hasattr(self.websocket, "closed")
                            and not self.websocket.closed
                        ):
                            await self.websocket.close()
                        elif (
                            hasattr(self.websocket, "state")
                            and self.websocket.state.name != "CLOSED"
                        ):
                            await self.websocket.close()
                        else:
                            # 如果没有closed属性，直接尝试关闭
                            await self.websocket.close()
                    except Exception:
                        # 如果关闭失败，忽略错误
                        pass
            except Exception as ws_error:
                self.logger.bind(tag=TAG).error(f"关闭WebSocket连接时出错: {ws_error}")

            if self.tts:
                await self.tts.close()

            # 最后关闭线程池（避免阻塞）
            if self.executor:
                try:
                    self.executor.shutdown(wait=False)
                except Exception as executor_error:
                    self.logger.bind(tag=TAG).error(
                        f"关闭线程池时出错: {executor_error}"
                    )
                self.executor = None

            self.logger.bind(tag=TAG).info("连接资源已释放")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"关闭连接时出错: {e}")
        finally:
            # 确保停止事件被设置
            if self.stop_event:
                self.stop_event.set()

    def clear_queues(self):
        """清空所有任务队列"""
        if self.tts:
            self.logger.bind(tag=TAG).debug(
                f"开始清理: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

            # 使用非阻塞方式清空队列
            for q in [
                self.tts.tts_text_queue,
                self.tts.tts_audio_queue,
                self.report_queue,
            ]:
                if not q:
                    continue
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

            self.logger.bind(tag=TAG).debug(
                f"清理结束: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

    def reset_vad_states(self):
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_voice_stop = False
        self.logger.bind(tag=TAG).debug("VAD states reset.")

    def chat_and_close(self, text):
        """Chat with the user and then close the connection"""
        try:
            # Use the existing chat method
            self.chat(text)

            # After chat is complete, close the connection
            self.close_after_chat = True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Chat and close error: {str(e)}")

    async def _check_timeout(self):
        """检查连接超时"""
        try:
            while not self.stop_event.is_set():
                # 检查是否超时（只有在时间戳已初始化的情况下）
                if getattr(self, "cc_keep_listening", False):
                    await asyncio.sleep(10)
                    continue
                if self.last_activity_time > 0.0:
                    current_time = time.time() * 1000
                    if (
                        current_time - self.last_activity_time
                        > self.timeout_seconds * 1000
                    ):
                        if not self.stop_event.is_set():
                            self.logger.bind(tag=TAG).info("连接超时，准备关闭")
                            # 设置停止事件，防止重复处理
                            self.stop_event.set()
                            # 使用 try-except 包装关闭操作，确保不会因为异常而阻塞
                            try:
                                await self.close(self.websocket)
                            except Exception as close_error:
                                self.logger.bind(tag=TAG).error(
                                    f"超时关闭连接时出错: {close_error}"
                                )
                        break
                # 每10秒检查一次，避免过于频繁
                await asyncio.sleep(10)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"超时检查任务出错: {e}")
        finally:
            self.logger.bind(tag=TAG).info("超时检查任务已退出")
