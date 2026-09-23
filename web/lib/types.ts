// Mirror of careconnect FastAPI response shapes (`/api/...`).
// All fields are camelCase per the envelope contract.

export type RiskLevel = "low" | "moderate" | "elevated" | "urgent";
export type ChatType = 1 | 2; // 1 = client, 2 = caregiver

export interface AgentSummary {
  id: string;
  agentName: string;
  agentCode: string | null;
  langCode: string | null;
  language: string | null;
  riskLevel: RiskLevel | null;
  createdAt: string | null;
}

export interface AgentDetail extends AgentSummary {
  systemPrompt: string | null;
  chatHistoryConf: number;
  llmModelId: string | null;
  ttsModelId: string | null;
  asrModelId: string | null;
  vadModelId: string | null;
  memModelId: string | null;
  intentModelId: string | null;
  vllmModelId?: string | null;
}

export interface ChatSession {
  sessionId: string;
  agentId: string;
  createdAt: string;
  messageCount: number;
  chatCount?: number;
}

export interface ChatMessage {
  id: number;
  chatType: ChatType;
  content: string;
  createdAt: string;
  audioId?: string | null;
  macAddress?: string | null;
}

export type DeviceType = "W1-A" | "W1-B";
export type FirmwareType = "sensecraft" | "xiaozhi";

export interface DeviceRow {
  id: string;
  macAddress: string;
  clientDeviceId: string | null; // optional external id from the client's system
  agentId: string | null;
  alias: string | null;
  board: string | null;
  deviceType: DeviceType | null;
  firmwareType: FirmwareType | null;
  lastConnectedAt: string | null;
  appVersion: string | null;
  autoUpdate: number;
  agentName?: string | null; // present in admin/all responses
}

export interface UnboundDevice {
  eui: string;
  lastSeen: string;
  sampleCount: number;
}

export interface MedicalAssessment {
  id: number;
  agentId: string;
  forDate: string;
  riskLevel: RiskLevel;
  confidence: number | null;
  concerns: string[];
  recommendations: string[];
  sourceMsgCount: number;
  llmModel: string;
  generatedAt: string;
}

export interface AdminSummary {
  id: number;
  username: string;
  role: number;
  roleName: "viewer" | "admin" | "root";
  status: number;
  createDate: string | null;
  scopedAgentCount: number;
  scopedAgentIds: string[];
}

export interface OnboardRequest {
  name: string;
  dob?: string | null;
  age?: number | null;
  condition?: string | null;
  tags?: string[];
  escalationPhrases?: string[];
  topicsToAvoid?: string[];
  personaOverride?: string | null;
  eui?: string | null;
  deviceAlias?: string | null;
  clientDeviceId?: string | null; // optional external id from the client's system
  deviceType?: DeviceType | null;
  firmwareType?: FirmwareType | null;
}

export type WsFrame =
  | { type: "hello"; payload: { agentId: string; serverTs: number; channels?: string[] } }
  | { type: "chat.turn"; payload: ChatMessage & { agentId: string; sessionId: string } }
  | { type: "assessment.updated"; payload: MedicalAssessment };

// ── Voice selector types ──────────────────────────────────────────────────────

export interface VoiceOption {
  id: string;
  label: string;
  engine: string;
  local: boolean;
  recommended: boolean;
}

export interface SpeedOption {
  id: string;
  label: string;
}

export interface VoiceCatalog {
  default: string;
  voices: VoiceOption[];
  speeds: SpeedOption[];
  default_speed: string;
  lengths: SpeedOption[];
  default_length: string;
}

export interface DeviceVoice {
  voice: string;
  speed: string;
  response_length?: string;
  volume?: number;
}
