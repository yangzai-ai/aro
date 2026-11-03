export interface ToolStep {
  call_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  success?: boolean;
  result?: unknown;
  error?: string | null;
  duration_ms?: number;
}

export interface AgentTrace {
  trace_id: string;
  thread_id: string;
  user_input: string;
  agent_response: string;
  tool_calls: ToolStep[];
  duration_ms: number;
  error: string | null;
}

export interface Approval {
  approval_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  expires_at: number;
}

export type AgentEvent =
  | { type: 'session'; data: { session_id: string } }
  | { type: 'token'; data: { delta: string } }
  | { type: 'tool_call' | 'tool_result'; data: ToolStep }
  | { type: 'approval_required'; data: Approval }
  | { type: 'done'; data: { session_id: string; trace: AgentTrace } }
  | { type: 'error'; data: { code: string; message: string; terminal: boolean } };

export interface ChatMessage { id: string; role: 'user' | 'assistant'; text: string }
