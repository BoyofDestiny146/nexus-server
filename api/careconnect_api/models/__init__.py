"""SQLAlchemy ORM models — mapped to the existing xiaozhi_esp32_server schema
plus the careconnect-only tables (cc_admin_client_access, future)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------- Existing XiaoZhi tables (mapped read-mostly) ----------

class SysUser(Base):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(50))
    password: Mapped[str | None] = mapped_column(String(100))
    super_admin: Mapped[int | None] = mapped_column(SmallInteger, default=0)
    status: Mapped[int | None] = mapped_column(SmallInteger, default=1)
    create_date: Mapped[datetime | None] = mapped_column(DateTime)
    update_date: Mapped[datetime | None] = mapped_column(DateTime)
    creator: Mapped[int | None] = mapped_column(BigInteger)
    updater: Mapped[int | None] = mapped_column(BigInteger)


class AiAgent(Base):
    __tablename__ = "ai_agent"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    agent_code: Mapped[str | None] = mapped_column(String(36))
    agent_name: Mapped[str | None] = mapped_column(String(64))
    bot_name: Mapped[str | None] = mapped_column(String(64))
    profile_json: Mapped[str | None] = mapped_column(Text)
    asr_model_id: Mapped[str | None] = mapped_column(String(32))
    vad_model_id: Mapped[str | None] = mapped_column(String(64))
    llm_model_id: Mapped[str | None] = mapped_column(String(32))
    vllm_model_id: Mapped[str | None] = mapped_column(String(32))
    tts_model_id: Mapped[str | None] = mapped_column(String(32))
    tts_voice_id: Mapped[str | None] = mapped_column(String(32))
    mem_model_id: Mapped[str | None] = mapped_column(String(32))
    intent_model_id: Mapped[str | None] = mapped_column(String(32))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    summary_memory: Mapped[str | None] = mapped_column(Text)
    chat_history_conf: Mapped[int] = mapped_column(SmallInteger, default=0)
    lang_code: Mapped[str | None] = mapped_column(String(10))
    language: Mapped[str | None] = mapped_column(String(10))
    sort: Mapped[int | None] = mapped_column(Integer, default=0)
    creator: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updater: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class AiDevice(Base):
    __tablename__ = "ai_device"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    mac_address: Mapped[str | None] = mapped_column(String(50))
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime)
    auto_update: Mapped[int | None] = mapped_column(SmallInteger, default=0)
    board: Mapped[str | None] = mapped_column(String(50))
    device_type: Mapped[str | None] = mapped_column(String(32))
    firmware_type: Mapped[str | None] = mapped_column(String(32))
    alias: Mapped[str | None] = mapped_column(String(64))
    # Optional external device identifier from the client's own provisioning
    # system. Lets careconnect resolve a device (and its patient) by the
    # client's unique id instead of our MAC/EUI. See migration 011.
    client_device_id: Mapped[str | None] = mapped_column(String(64), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(32))
    app_version: Mapped[str | None] = mapped_column(String(20))
    sort: Mapped[int | None] = mapped_column(Integer, default=0)
    creator: Mapped[int | None] = mapped_column(BigInteger)
    create_date: Mapped[datetime | None] = mapped_column(DateTime)
    updater: Mapped[int | None] = mapped_column(BigInteger)
    update_date: Mapped[datetime | None] = mapped_column(DateTime)
    # Watcher telemetry — populated by the client heartbeat API.
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    battery: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    fw: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rssi: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class AiAgentChatHistory(Base):
    __tablename__ = "ai_agent_chat_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mac_address: Mapped[str | None] = mapped_column(String(50))
    agent_id: Mapped[str | None] = mapped_column(String(32))
    session_id: Mapped[str | None] = mapped_column(String(50))
    chat_type: Mapped[int | None] = mapped_column(
        SmallInteger
    )  # 1=client, 2=caregiver/assistant, 3=system_event (e.g. google_calendar)
    content: Mapped[str | None] = mapped_column(String(1024))
    audio_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )


class AiMedicalAssessment(Base):
    __tablename__ = "ai_medical_assessment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    for_date: Mapped[Date] = mapped_column(Date, index=True)
    risk_level: Mapped[str] = mapped_column(String(16))  # low / moderate / elevated / urgent
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    concerns_json: Mapped[str | None] = mapped_column(Text)  # JSON array of strings
    recommendations_json: Mapped[str | None] = mapped_column(Text)
    source_msg_count: Mapped[int | None] = mapped_column(Integer)
    llm_model: Mapped[str | None] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ---------- careconnect-only tables ----------

class AdminClientAccess(Base):
    """Per-admin client scoping. Root admin (super_admin=2) ignores this table."""
    __tablename__ = "cc_admin_client_access"

    admin_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    granted_by: Mapped[int] = mapped_column(BigInteger)


class ClientIntegration(Base):
    """Per-client partner identity (CareConnect, Revel, later V6G / sensors).

    Belongs to ``ai_agent``, not to a Watcher. Disconnect deletes this row only.
    """

    __tablename__ = "cc_client_integration"
    __table_args__ = (
        UniqueConstraint("agent_id", "provider", name="uq_cc_integration_agent_provider"),
        UniqueConstraint("public_id", name="uq_cc_integration_public_id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    agent_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    public_id: Mapped[str | None] = mapped_column(String(32))
    secret_hash: Mapped[str | None] = mapped_column(String(128))
    secret_enc: Mapped[str | None] = mapped_column(Text)
    secret_hint: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="connected")
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeBase(Base):
    """Reusable localized knowledge catalog. Assigned to clients (ai_agent),
    not stored on ai_device. Watchers inherit via the bound agent."""

    __tablename__ = "cc_knowledge_base"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_cc_knowledge_base_slug"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(512))
    enabled: Mapped[int] = mapped_column(SmallInteger, default=1)
    knowledge_type: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeTopic(Base):
    """Topic inside a Knowledge Base. Revel fields are metadata only in Phase 1."""

    __tablename__ = "cc_knowledge_topic"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "topic_key", name="uq_cc_knowledge_topic_key"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    knowledge_base_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("cc_knowledge_base.id", ondelete="CASCADE"),
        index=True,
    )
    topic_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[int] = mapped_column(SmallInteger, default=1)
    revel_tag: Mapped[str | None] = mapped_column(String(128))
    revel_auto_trigger: Mapped[int] = mapped_column(SmallInteger, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ClientKnowledgeBase(Base):
    """Many-to-many: client (ai_agent.id) ↔ reusable Knowledge Base."""

    __tablename__ = "cc_client_knowledge_base"

    agent_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    knowledge_base_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("cc_knowledge_base.id", ondelete="CASCADE"),
        primary_key=True,
    )
    enabled: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())


class KnowledgeSource(Base):
    """File or manual text belonging to a Knowledge Base. Phase 2 stores the
    original only — no embeddings. topic_id is optional metadata; deleting a
    source must not delete topics (ON DELETE SET NULL)."""

    __tablename__ = "cc_knowledge_source"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    knowledge_base_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("cc_knowledge_base.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160))
    source_type: Mapped[str] = mapped_column(String(16))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    storage_path: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(String(512))
    enabled: Mapped[int] = mapped_column(SmallInteger, default=1)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"))
    topic_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("cc_knowledge_topic.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
