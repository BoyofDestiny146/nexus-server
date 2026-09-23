import openai
import json
from config.logger import setup_logging
from core.utils.util import check_model_key
from core.providers.vllm.base import VLLMProviderBase
from core.utils.ollama_env import (
    ON_DEMAND_KEEP_ALIVE,
    looks_like_local_ollama,
    ollama_v1_url,
    resolve_ollama_base_url,
)

TAG = __name__
logger = setup_logging()


class VLLMProvider(VLLMProviderBase):
    def __init__(self, config):
        self.model_name = config.get("model_name")
        self.api_key = config.get("api_key")
        if "base_url" in config:
            self.base_url = config.get("base_url")
        else:
            self.base_url = config.get("url")

        if looks_like_local_ollama(self.base_url):
            resolved, src = resolve_ollama_base_url(self.base_url)
            self.base_url = ollama_v1_url(resolved)
            logger.bind(tag=TAG).info(
                f"OllamaVLLM selected source={src} model={self.model_name} "
                f"base_url={self.base_url}"
            )

        param_defaults = {
            "max_tokens": (500, int),
            "temperature": (0.7, lambda x: round(float(x), 1)),
            "top_p": (1.0, lambda x: round(float(x), 1)),
        }

        for param, (default, converter) in param_defaults.items():
            value = config.get(param)
            try:
                setattr(
                    self,
                    param,
                    converter(value) if value not in (None, "") else default,
                )
            except (ValueError, TypeError):
                setattr(self, param, default)

        model_key_msg = check_model_key("VLLM", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)
        self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)

    def response(self, question, base64_image):
        # careconnect: English-only (the reply is spoken via the English TTS; a
        # CJK reply would be blanked by the CJK-strip guard). Keep it short.
        question = question + "\n\nReply ONLY in English, in 1-2 short sentences, describing what you see."
        try:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ]

            create_kwargs = {
                "model": self.model_name,
                "messages": messages,
                "stream": False,
            }
            if looks_like_local_ollama(self.base_url):
                create_kwargs["extra_body"] = {"keep_alive": ON_DEMAND_KEEP_ALIVE}
            response = self.client.chat.completions.create(**create_kwargs)

            return response.choices[0].message.content

        except Exception as e:
            logger.bind(tag=TAG).error(f"Error in response generation: {e}")
            raise
