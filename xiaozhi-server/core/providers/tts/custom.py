import os
import json
import uuid
import requests
from urllib.parse import urlsplit, urlunsplit
from config.logger import setup_logging
from datetime import datetime
from core.providers.tts.base import TTSProviderBase

TAG = __name__
logger = setup_logging()


def resolve_custom_tts_url(config_url, environ=None):
    """Pick the CustomTTS endpoint: CC_TTS_URL > PIPER_URL > YAML ``url``.

    Empty env values are treated as unset so a blank override cannot
    silently disable TTS. Returns ``(url, source)`` where source is
    ``CC_TTS_URL``, ``PIPER_URL``, or ``yaml``.
    """
    env = os.environ if environ is None else environ
    for key in ("CC_TTS_URL", "PIPER_URL"):
        val = (env.get(key) or "").strip()
        if val:
            return val, key
    yaml_url = (config_url or "").strip() if isinstance(config_url, str) else (config_url or None)
    if yaml_url:
        return yaml_url, "yaml"
    return None, "yaml"


def safe_tts_url_for_log(url):
    """Host + path only — drop userinfo, query, and fragment (no secrets)."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except Exception:
        return "<unparseable>"


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        yaml_url = config.get("url")
        self.url, url_source = resolve_custom_tts_url(yaml_url)
        self.method = config.get("method", "GET")
        self.headers = config.get("headers", {})
        self.format = config.get("format", "wav")
        self.audio_file_type = config.get("format", "wav")
        self.output_file = config.get("output_dir", "tmp/")
        self.params = config.get("params")

        if isinstance(self.params, str):
            try:
                self.params = json.loads(self.params)
            except json.JSONDecodeError:
                raise ValueError("Custom TTS配置参数出错,无法将字符串解析为对象")
        elif not isinstance(self.params, dict):
            raise TypeError("Custom TTS配置参数出错, 请参考配置说明")

        logger.bind(tag=TAG).info(
            f"CustomTTS endpoint selected source={url_source} url={safe_tts_url_for_log(self.url)}"
        )

    def generate_filename(self):
        return os.path.join(self.output_file, f"tts-{datetime.now().date()}@{uuid.uuid4().hex}.{self.format}")

    async def text_to_speak(self, text, output_file):
        request_params = {}
        for k, v in self.params.items():
            if isinstance(v, str) and "{prompt_text}" in v:
                v = v.replace("{prompt_text}", text)
            request_params[k] = v

        if self.method.upper() == "POST":
            resp = requests.post(self.url, json=request_params, headers=self.headers)
        else:
            resp = requests.get(self.url, params=request_params, headers=self.headers)
        if resp.status_code == 200:
            if output_file:
                with open(output_file, "wb") as file:
                    file.write(resp.content)
            else:
                return resp.content
        else:
            error_msg = f"Custom TTS请求失败: {resp.status_code} - {resp.text}"
            logger.bind(tag=TAG).error(error_msg)
            raise Exception(error_msg)  # 抛出异常，让调用方捕获
