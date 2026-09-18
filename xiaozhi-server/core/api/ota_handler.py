import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from aiohttp import web

from core.utils.util import get_local_ip
from core.api.base_handler import BaseHandler

TAG = __name__


# careconnect-deployed xiaozhi-server URLs returned to the device.
#
# We use plain ws:// (NOT wss://) and bypass Caddy because the XiaoZhi
# firmware bundles ESP's curated x509 trust store (LetsEncrypt + major CAs)
# and rejects our Caddy local-CA self-signed cert with mbedtls -0x3000
# (handshake failure). On the LAN we don't need TLS — the careconnect
# deployment runs on a private network with no upstream egress.
CARECONNECT_WS_URL = os.environ.get("CC_XIAOZHI_WS_URL", "ws://localhost:8000/xiaozhi/v1/")

# MQTT goes to xiaozhi-mqtt-gateway (Node.js), NOT a bare Mosquitto. The
# gateway brokers MQTT/UDP signaling AND brokers each session over to our
# xiaozhi-server WS. ``host:port`` format avoids the firmware abort that
# triggered when we passed an explicit ``mqtt://...`` scheme.
CARECONNECT_MQTT_HOST = os.environ.get("CC_MQTT_PUBLIC_IP", "localhost")
CARECONNECT_MQTT_PORT = 1883
CARECONNECT_MQTT_ENDPOINT = os.environ.get(
    "CC_MQTT_ENDPOINT", f"{CARECONNECT_MQTT_HOST}:{CARECONNECT_MQTT_PORT}")
CARECONNECT_MQTT_GROUP = "GID_careconnect"

# Transport blocks returned in the OTA response:
#   "both"      -> websocket + mqtt (LAN co-located default; firmware uses MQTT)
#   "websocket" -> websocket only. Use for the remote-over-internet deployment:
#                  the device reaches us through the Cloudflare tunnel, and raw
#                  MQTT can't traverse an HTTP tunnel, so we omit the mqtt block
#                  and the firmware falls back to the WebSocket transport.
#   "mqtt"      -> mqtt only.
CARECONNECT_OTA_TRANSPORT = os.environ.get("CC_OTA_TRANSPORT", "both").lower()

# MUST match xiaozhi-mqtt-gateway's MQTT_SIGNATURE_KEY env var. The gateway
# rejects connect attempts whose password doesn't HMAC-SHA256 the
# ``client_id|username`` content with this key.
#
# Resolution order (env-override-first, matching careconnect_db._find_secret):
#   1. MQTT_SIGNATURE_KEY_FILE -> read raw file contents (the docker secret at
#      /run/secrets/mqtt-signature-key). Production path; avoids the HOME-relative
#      read that failed in the root container (the cause of the docker-commit hack).
#   2. ~/.config/careconnect/secrets/xiaozhi-mqtt-gateway.env MQTT_SIGNATURE_KEY=
#      line — bare-metal / dev fallback.
_GATEWAY_ENV = Path.home() / ".config" / "careconnect" / "secrets" / "xiaozhi-mqtt-gateway.env"

_signature_key_cache: str | None = None


def _signature_key() -> str:
    """Resolve the MQTT signature key once (secret file first, env file fallback)."""
    global _signature_key_cache
    if _signature_key_cache is not None:
        return _signature_key_cache
    key_file = os.environ.get("MQTT_SIGNATURE_KEY_FILE")
    if key_file and Path(key_file).exists():
        _signature_key_cache = Path(key_file).read_text().strip()
        return _signature_key_cache
    if _GATEWAY_ENV.exists():
        for line in _GATEWAY_ENV.read_text().splitlines():
            if line.startswith("MQTT_SIGNATURE_KEY="):
                _signature_key_cache = line.split("=", 1)[1].strip()
                return _signature_key_cache
    raise RuntimeError(
        f"MQTT signature key not found (MQTT_SIGNATURE_KEY_FILE={key_file!r}, {_GATEWAY_ENV})"
    )


class OTAHandler(BaseHandler):
    def __init__(self, config: dict):
        super().__init__(config)

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket", "")

        # If the operator hardcoded a websocket URL in config and didn't leave
        # the upstream "你的IP/域名" placeholder, honor it. Otherwise return
        # our LAN ws:// URL (bypass Caddy — see top-of-file note).
        if websocket_config and "你的" not in websocket_config:
            return websocket_config
        return CARECONNECT_WS_URL

    def _build_mqtt_block(self, device_id: str, data_json: dict, client_ip: str) -> dict:
        """Build the MQTT activation block returned to xiaozhi-esp32 firmware.

        Algorithm matches xiaozhi-mqtt-gateway/utils/mqtt_config_v2.js
        ``generateMqttConfig`` exactly so the gateway accepts the device's
        connect packet:

          client_id = ``GID_careconnect@@@<mac-with-_>@@@<board-uuid>``
          username  = base64( JSON.stringify({"ip": "<device-lan-ip>"}) )
          password  = base64( HMAC-SHA256( client_id|username, MQTT_SIGNATURE_KEY ) )

        Endpoint format is ``host:port`` (no scheme) — the firmware aborts
        on ``mqtt://...`` schemes (verified at PC 0x4214e65b) but the
        ``host:port`` form lets it use plain TCP on the supplied port.

        The ``subscribe_topic`` value of literal string ``"null"`` is
        intentional: older firmware versions error out if the field is
        missing (per gateway source comment). Real per-device topic is
        assigned by the gateway after MQTT CONNECT.
        """
        mac = (device_id or "").lower()
        mac_no_colon = mac.replace(":", "_")
        board_uuid = ""
        try:
            board_uuid = (data_json.get("board") or {}).get("uuid", "") or ""
        except Exception:
            pass

        client_id = f"{CARECONNECT_MQTT_GROUP}@@@{mac_no_colon}@@@{board_uuid}"
        user_data = {"ip": client_ip or ""}
        # JSON serialization matches Node's JSON.stringify default (no
        # whitespace, sorted by insertion order — single-key dict is fine).
        username_json = json.dumps(user_data, separators=(",", ":"))
        username = base64.b64encode(username_json.encode("utf-8")).decode("ascii")

        signature_key = _signature_key()
        content = f"{client_id}|{username}"
        digest = hmac.new(
            signature_key.encode("utf-8"),
            content.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        password = base64.b64encode(digest).decode("ascii")

        return {
            "endpoint": CARECONNECT_MQTT_ENDPOINT,
            "client_id": client_id,
            "username": username,
            "password": password,
            "publish_topic": "device-server",
            "subscribe_topic": "null",
        }

    async def handle_post(self, request):
        """处理 OTA POST 请求"""
        try:
            data = await request.text()
            self.logger.bind(tag=TAG).debug(f"OTA请求方法: {request.method}")
            self.logger.bind(tag=TAG).debug(f"OTA请求头: {request.headers}")
            self.logger.bind(tag=TAG).debug(f"OTA请求数据: {data}")

            device_id = request.headers.get("device-id", "")
            if device_id:
                self.logger.bind(tag=TAG).info(f"OTA请求设备ID: {device_id}")
            else:
                raise Exception("OTA请求设备ID为空")

            data_json = json.loads(data)

            server_config = self.config["server"]
            port = int(server_config.get("port", 8000))
            local_ip = get_local_ip()

            # Best-effort device source IP for the username payload. Falls
            # back to the LAN IP we know about if reverse-proxy headers
            # don't show through (Caddy adds X-Forwarded-For when it does).
            client_ip = (
                request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or (request.remote or "")
                or ""
            )

            return_json = {
                "server_time": {
                    "timestamp": int(round(time.time() * 1000)),
                    "timezone_offset": server_config.get("timezone_offset", 8) * 60,
                },
                "firmware": {
                    "version": data_json["application"].get("version", "1.0.0"),
                    "url": "",
                },
                "websocket": {
                    "url": self._get_websocket_url(local_ip, port),
                },
            }
            # Only include the MQTT block when MQTT transport is enabled. For the
            # remote-over-internet deployment (CC_OTA_TRANSPORT=websocket) we omit
            # it so the firmware uses the tunnel-reachable WebSocket endpoint.
            if CARECONNECT_OTA_TRANSPORT in ("both", "mqtt"):
                return_json["mqtt"] = self._build_mqtt_block(
                    device_id, data_json, client_ip
                )
            response = web.Response(
                text=json.dumps(return_json, separators=(",", ":")),
                content_type="application/json",
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"OTA POST请求异常: {e}")
            return_json = {"success": False, "message": "request error."}
            response = web.Response(
                text=json.dumps(return_json, separators=(",", ":")),
                content_type="application/json",
            )
        finally:
            self._add_cors_headers(response)
            return response

    async def handle_get(self, request):
        """处理 OTA GET 请求"""
        try:
            server_config = self.config["server"]
            local_ip = get_local_ip()
            port = int(server_config.get("port", 8000))
            websocket_url = self._get_websocket_url(local_ip, port)
            message = f"OTA接口运行正常，向设备发送的websocket地址是：{websocket_url}"
            response = web.Response(text=message, content_type="text/plain")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"OTA GET请求异常: {e}")
            response = web.Response(text="OTA接口异常", content_type="text/plain")
        finally:
            self._add_cors_headers(response)
            return response
