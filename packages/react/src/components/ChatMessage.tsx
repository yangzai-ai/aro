import type { ReactNode } from 'react';
import type { ToolStep } from '@aro/client';
import { ThinkingBlock } from './ThinkingBlock.js';
import { ToolCallCard } from './ToolCallCard.js';
export interface ChatMessageProps {
  role: 'user' | 'assistant'; content: string; thinking?: string; toolCalls?: ToolStep[];
  label?: string; busy?: boolean; beforeContent?: ReactNode; renderContent?: (content: string) => ReactNode;
}
export function ChatMessage({role,content,thinking,toolCalls,label,busy=false,beforeContent,renderContent}: ChatMessageProps) {
  return <article className={`message ${role}`}>
    <div className="message-label">{label ?? (role === 'user' ? '你' : '助手')}</div>
    {beforeContent}{role === 'assistant' && thinking && <ThinkingBlock content={thinking} active={busy} />}
    <div className="message-text">{content ? (renderContent ? renderContent(content) : content) : (busy ? '正在思考或等待工具结果…' : '本轮没有文本回复。')}</div>
    {!!toolCalls?.length && <ol className="steps">{toolCalls.map((step,index) => <li key={step.call_id}><ToolCallCard step={step} index={index} busy={busy} /></li>)}</ol>}
  </article>;
}
