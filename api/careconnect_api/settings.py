"""Settings — read from env vars + secret files in ~/.config/careconnect/."""
from __future__ import annotations

import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


CFG_DIR = Path.home() / ".config" / "careconnect"


def _read_or_create_secret(path: Path, generator=lambda: secrets.token_urlsafe(48)) -> str:
    """Read a secret file at `path`. If missing, generate one and write it (mode 0600)."""
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = generator()
    path.write_text(value)
    path.chmod(0o600)
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CC_", extra="ignore")

    # Server
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "info"

    # MariaDB
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "xiaozhi"
    db_name: str = "xiaozhi_esp32_server"
    db_password_file: Path = CFG_DIR / "mariadb-app"

    # Redis (pub/sub for WebSocket fan-out)
    redis_url: str = "redis://127.0.0.1:6379/0"

    # JWT
    jwt_secret_file: Path = CFG_DIR / "api-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_ttl_hours: int = 12

    # Internal API calls (e.g., chat-turn notify from xiaozhi-server)
    internal_token_file: Path = CFG_DIR / "api-internal-token"

    # Client-facing API key (watcher heartbeat / status / list endpoints)
    client_api_key_file: Path = CFG_DIR / "client-api-key"

    # Watcher online window: a device is considered online if last_seen is
    # within this many seconds of the current UTC time.
    watcher_online_window_seconds: int = 120

    # Ollama (used by triage scheduler)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_triage_model: str = "llama3.1:8b-instruct-q4_K_M"
    ollama_timeout_s: float = 60.0
    # Conversational Watcher model (xiaozhi-server OllamaLLM). Health probes
    # this, not the triage model. Compose default is qwen2.5:3b.
    llm_model: str = "qwen2.5:3b"

    # Service endpoints probed by /api/health/checks
    # Defaults match docker-compose service names; override via CC_* env vars.
    xiaozhi_server_host: str = "xiaozhi-server"
    xiaozhi_ota_port: int = 8003
    xiaozhi_ws_port: int = 8000
    mqtt_host: str = "xiaozhi-mqtt-gateway"
    mqtt_port: int = 1883
    mqtt_admin_port: int = 8007
    piper_host: str = "piper-tts"
    piper_port: int = 5500
    # Public OTA hostname for display only — never the primary health probe
    # (hairpin NAT from the Orin would false-fail). Matches Caddy + firmware.
    ota_public_url: str = "https://ota.nexus.warehouse-13.biz/xiaozhi/ota/"
    # Probe timeouts (seconds)
    health_probe_timeout_s: float = 3.0
    health_total_timeout_s: float = 5.0

    # Triage scheduler
    triage_cron_hour: int = 2
    triage_cron_minute: int = 0
    triage_history_window_hours: int = 24
    triage_max_messages: int = 50

    # W1-A / XiaoZhi ingest path — always enabled (W1-B bridge is removed).
    xiaozhi_ingest_enabled: bool = True

    # Multi-engine TTS server (kokoro + piper + edge, OpenAI-compatible)
    tts_url: str = "http://127.0.0.1:5500"
    # Per-device voice config JSON — read/written by the voice router.
    # The xiaozhi-server reads the same file via CC_VOICE_CONFIG in its env.
    voice_config_path: str = "/data/voice_config.json"
    # Knowledge source originals. Same Docker volume as voice_config (/data,
    # compose: cc-voice). Never served publicly; download is JWT-gated.
    knowledge_source_dir: str = "/data/knowledge-sources"

    # Bootstrap admin credentials — read from env or secret files.
    # Usernames are fixed as admin1 / admin2 (generic, not personal).
    admin1_password_file: Path = CFG_DIR / "admin1-password"
    admin2_password_file: Path = CFG_DIR / "admin2-password"

    # Fernet key for Revel credentials and optional CareConnect secret_enc
    # (outbound push only). GET/dashboard CareConnect auth uses bcrypt hash.
    integration_secret_key_file: Path = CFG_DIR / "integration-secret-key"

    # Public CareConnect portal (connection package + partner GET base).
    portal_base_url: str = "https://care.nexus.warehouse-13.biz"
    # Used when serializing partner assessment timestamps. Matches compose TZ.
    tz: str = "America/Chicago"
    # Outbound push destination. Empty (default) = no outbound HTTP.
    # Set only to a *separate* CareConnect receiver. Same-host URLs are skipped.
    careconnect_ingest_url: str = ""
    careconnect_push_timeout_s: float = 5.0

    # Google Calendar (read-only iCal). Poller speaks timed occurrences only.
    gcal_poll_seconds: int = 60
    gcal_due_lookback_seconds: int = 90
    gcal_due_lookahead_seconds: int = 15
    gcal_fetch_timeout_s: float = 10.0
    gcal_max_ics_bytes: int = 2_000_000
    gcal_fired_retain_days: int = 14
    gcal_upcoming_days: int = 14

    # ----- derived (lazy) -----
    @property
    def careconnect_assessment_url(self) -> str:
        return (
            f"{self.portal_base_url.rstrip('/')}"
            "/api/v1/integrations/careconnect/assessment"
        )

    @property
    def db_password(self) -> str:
        return self.db_password_file.read_text().strip()

    @property
    def jwt_secret(self) -> str:
        return _read_or_create_secret(self.jwt_secret_file)

    @property
    def internal_token(self) -> str:
        return _read_or_create_secret(self.internal_token_file)

    @property
    def client_api_key(self) -> str:
        return _read_or_create_secret(self.client_api_key_file)

    @property
    def db_url_async(self) -> str:
        # SQLAlchemy async URL via aiomysql
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )


settings = Settings()
