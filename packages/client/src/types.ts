export interface HookError { hook_class: string; hook: string; error_type: string }
export interface ToolCallEvent {
  call_id: string; tool_name: string; args: Record<string, unknown>;
  agent_name?: string; parent_call_id?: string | null;
}
export interface ToolResultEvent extends ToolCallEvent {
  success: boolean; result: unknown; error: string | null; duration_ms: number;
  approval_duration_ms: number | null; execution_duration_ms: number; hook_errors: HookError[];
}
/** A UI step can exist before its tool_result arrives. */
export interface ToolStep extends ToolCallEvent {
  success?: boolean; result?: unknown; error?: string | null; duration_ms?: number;
  approval_duration_ms?: number | null; execution_duration_ms?: number; hook_errors?: HookError[];
  start_time?: number; end_time?: number;
}
export interface AgentTrace {
  trace_id: string; thread_id: string | null; user_input: string; agent_response: string;
  tool_calls: ToolStep[]; duration_ms: number | null; error: string | null;
  start_time?: number; end_time?: number | null;
}
export interface TokenEvent { delta: string }
export interface AgentSource { agent_name: string; parent_call_id: string | null }
export interface Approval {
  approval_id: string; tool_name: string; args: Record<string, unknown>;
  expires_at: number; thread_id?: string;
}
export interface EventMap {
  token: TokenEvent; reasoning: TokenEvent;
  tool_call: ToolCallEvent; tool_result: ToolResultEvent;
  agent_start: AgentSource; agent_done: AgentSource & { trace: AgentTrace | Record<string, never> };
  agent_failed: AgentSource; agent_token: AgentSource & TokenEvent; agent_reasoning: AgentSource & TokenEvent;
  session: { session_id: string }; done: { session_id: string; trace: AgentTrace | null };
  error: { code: string; message: string; terminal: boolean; approval_id?: string };
  approval_required: Approval;
}
export type AroEventType = keyof EventMap;
export type AroEvent = { [K in AroEventType]: { type: K; data: EventMap[K] } }[AroEventType];
export type AgentEvent = AroEvent;
