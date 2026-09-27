import { PromptContent, PromptMessageRole } from "../../types";

export type CommitHash = string;

export type VariantStatus = "generating" | "complete" | "cancelled" | "error";

export type AgentEventStatus = "running" | "complete" | "error";
export type AgentEventType = "thinking" | "assistant" | "tool";

/** One image as the agent tools report it; the backend fills what applies. */
export type ToolImage = {
  image_url?: string;
  original_image_url?: string;
  public_url?: string;
  result_url?: string;
  url?: string;
  asset_id?: string;
  prompt?: string;
  aspect_ratio?: string;
  image_urls?: string[];
  old_text?: string;
  new_text?: string;
  replaced?: number;
  status?: string;
  error?: string;
  [key: string]: unknown;
};

/**
 * A tool call's input or output. Every field is optional because the shape
 * depends on the tool; the index signature keeps unknown tools readable
 * without weakening the fields that are known.
 */
export type ToolPayload = {
  images?: ToolImage[];
  edits?: ToolImage[];
  screenshots?: ToolImage[];
  assets?: unknown[];
  prompts?: string[];
  image_urls?: string[];
  asset_ids?: string[];
  asset_descriptions?: string[];
  count?: number;
  error?: string;
  [key: string]: unknown;
};

export type AgentEvent = {
  id: string;
  type: AgentEventType;
  status: AgentEventStatus;
  content?: string;
  toolName?: string;
  input?: ToolPayload;
  output?: ToolPayload;
  startedAt: number;
  endedAt?: number;
};

export type VariantHistoryMessage = {
  role: PromptMessageRole;
  text: string;
  imageAssetIds: string[];
  videoAssetIds: string[];
};

export type Variant = {
  code: string;
  history: VariantHistoryMessage[];
  requestStartedAt?: number;
  completedAt?: number;
  status?: VariantStatus;
  errorMessage?: string;
  thinking?: string;
  thinkingStartTime?: number;
  thinkingDuration?: number;
  agentEvents?: AgentEvent[];
  model?: string;
};

export type BaseCommit = {
  hash: CommitHash;
  parentHash: CommitHash | null;
  dateCreated: Date;
  isCommitted: boolean;
  variants: Variant[];
  selectedVariantIndex: number;
  /**
   * Human-readable name for this entry, used instead of "Version N" when
   * present. Set for cloned site pages so each page is identifiable.
   */
  label?: string;
};

export type CommitType = "ai_create" | "ai_edit" | "code_create";

export type AiCreateCommit = BaseCommit & {
  type: "ai_create";
  inputs: PromptContent;
};

export type AiEditCommit = BaseCommit & {
  type: "ai_edit";
  inputs: PromptContent;
};

export type CodeCreateCommit = BaseCommit & {
  type: "code_create";
  inputs: null;
};

export type Commit = AiCreateCommit | AiEditCommit | CodeCreateCommit;
