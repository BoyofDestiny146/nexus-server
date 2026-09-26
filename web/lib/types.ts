// Mirror of careconnect FastAPI response shapes (`/api/...`).
// All fields are camelCase per the envelope contract.

export type RiskLevel = "low" | "moderate" | "elevated" | "urgent";
export type ChatType = 1 | 2 | 3; // 1 = client, 2 = caregiver, 3 = system_event

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
  botName?: string | null;
  systemPrompt: string | null;
  chatHistoryConf: number;
  llmModelId: string | null;
  ttsModelId: string | null;
  asrModelId: string | null;
  vadModelId: string | null;
  memModelId: string | null;
  intentModelId: string | null;
  vllmModelId?: string | null;
  dob?: string | null;
  age?: number | null;
  condition?: string | null;
  tags?: string[];
  escalationPhrases?: string[];
  topicsToAvoid?: string[];
  personaOverride?: string | null;
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
  botName?: string | null;
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
  sleepTimeoutSec?: number;
  listenScreenOff?: boolean;
  sleepMode?: "screen_off" | "deep_sleep";
  powerApplied?: {
    sleepTimeoutSec: number;
    listenScreenOff: boolean;
    sleepMode: "screen_off" | "deep_sleep";
  } | null;
  powerApplyState?: "unset" | "pending_offline" | "pending_ack" | "applied";
  powerSaved?: boolean;
  powerSaveMessage?: string;
}

export type IntegrationProvider = "careconnect" | "revel" | "directed_logic" | "google_calendar";
export type IntegrationStatus = "connected" | "disconnected" | "coming_soon";

export interface CalendarEventPreview {
  title: string;
  start: string;
  end?: string | null;
  allDay?: boolean;
  location?: string;
}

export interface RevelDiscoveredDevice {
  id: string;
  name: string;
  isOnline?: boolean | null;
  tags?: string[];
}

export interface RevelVoiceAction {
  intent: string;
  label?: string | null;
  revelTag?: string | null;
  enabled?: boolean;
  phrases?: string[];
}

export interface ClientIntegration {
  provider: IntegrationProvider;
  label: string;
  status: IntegrationStatus;
  connected: boolean;
  comingSoon?: boolean;
  publicId?: string | null;
  secretMasked?: boolean;
  secret?: string;
  secretOnce?: boolean;
  secretHint?: string | null;
  maskedKey?: string | null;
  portal?: string | null;
  assessmentEndpoint?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  replaced?: boolean;
  access?: "read_only";
  calendarHost?: string | null;
  lastSuccessfulSync?: string | null;
  lastSyncError?: string | null;
  nextEvent?: CalendarEventPreview | null;
  upcoming?: CalendarEventPreview[];
  ok?: boolean;
  eventCount?: number;
  apiBaseUrl?: string | null;
  deviceId?: string | null;
  deviceName?: string | null;
  discoveredDevices?: RevelDiscoveredDevice[];
  discoveredTags?: string[];
  lastDiscoverAt?: string | null;
  registrationKeySet?: boolean;
  registrationKeyHint?: string | null;
  actions?: RevelVoiceAction[];
  executeEnabled?: boolean;
  voiceRequiresBotName?: boolean;
}

export interface KnowledgeTopic {
  id: number;
  knowledgeBaseId: number;
  topicKey: string;
  title: string;
  description: string | null;
  enabled: boolean;
  revelTag: string | null;
  revelAutoTrigger: boolean;
  sortOrder: number;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface KnowledgeBase {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  enabled: boolean;
  knowledgeType: string | null;
  topicCount?: number;
  topics?: KnowledgeTopic[];
  assigned?: boolean;
  assignmentEnabled?: boolean;
  sourceCount?: number;
  clientCount?: number;
  assignedClients?: KnowledgeAssignedClient[];
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface KnowledgeAssignedClient {
  id: string;
  agentName: string | null;
  assignmentEnabled: boolean;
}

export interface KnowledgeSource {
  id: number;
  knowledgeBaseId: number;
  name: string;
  sourceType: KnowledgeSourceType;
  originalFilename: string | null;
  description: string | null;
  enabled: boolean;
  status: KnowledgeSourceStatus;
  mimeType: string | null;
  fileSize: number | null;
  topicId: number | null;
  topicTitle: string | null;
  topicKey: string | null;
  revelTag: string | null;
  errorMessage: string | null;
  hasFile: boolean;
  storagePath?: string | null;
  chunkCount?: number;
  indexedAt?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export type KnowledgeSourceType = "pdf" | "docx" | "pptx" | "txt" | "markdown" | "image" | "text";
export type KnowledgeSourceStatus = "uploaded" | "pending" | "processing" | "ready" | "failed" | "disabled";

export interface KnowledgeSourceList {
  list: KnowledgeSource[];
  total: number;
}

export interface KnowledgeTestHit {
  sourceId: number;
  source: string;
  sourceType: string;
  topic: string | null;
  topicKey: string | null;
  matchedText: string;
  score: number;
  revelTag: string | null;
  pageNumber?: number | null;
  slideNumber?: number | null;
  revelAutoTrigger?: boolean;
}

export interface KnowledgeTestSearch {
  mode: string;
  retrievalConfigured: boolean;
  query: string;
  list: KnowledgeTestHit[];
  total: number;
  message: string | null;
}

export interface KnowledgeSearchHit {
  knowledgeBaseId: number;
  sourceId: number;
  sourceName: string;
  topicId: number | null;
  topic?: string | null;
  text: string;
  score: number | null;
  pageNumber: number | null;
  slideNumber: number | null;
  revelTag: string | null;
  revelAutoTrigger: boolean;
}

export interface KnowledgeSearchResponse {
  query: string;
  results: KnowledgeSearchHit[];
}

export interface KnowledgeBaseList {
  list: KnowledgeBase[];
  total: number;
}

export interface DeviceKnowledgeContext {
  deviceMac: string;
  clientId: string | null;
  knowledgeBases: Array<{
    id: number;
    slug: string;
    name: string;
    topics: Array<{
      topicKey: string;
      title: string;
      revelTag: string | null;
      revelAutoTrigger: boolean;
    }>;
  }>;
}
