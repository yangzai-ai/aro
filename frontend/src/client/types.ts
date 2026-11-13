export type { ToolStep, AgentTrace, Approval, AgentEvent } from '@aro/client';

export interface ReasoningBlock {
  id: string;
  agent?: string;
  text: string;
  duration_ms: number;
  complete: boolean;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  reasoning_blocks?: ReasoningBlock[];
}
