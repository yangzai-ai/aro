import { useCallback, useEffect, useRef, useState } from 'react';
import type { AgentTrace, Approval, AroClient, AroEvent, ToolStep } from '@aro/client';

export interface ReasoningBlock {
  id: string; agent?: string; content: string; durationMs: number; complete: boolean;
}
export interface Message {
  id: string; role: 'user' | 'assistant'; content: string; reasoning: ReasoningBlock[];
}
export interface UseAroChatOptions {
  client: AroClient; sessionId?: string | null; targetAgent?: string | null;
  onEvent?: (event: AroEvent) => void;
}
export function useAroChat({client, sessionId = null, targetAgent = null, onEvent}: UseAroChatOptions) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [toolCalls, setToolCalls] = useState<ToolStep[]>([]);
  const [trace, setTrace] = useState<AgentTrace | null>(null);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [isLoading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [identity, setIdentity] = useState(sessionId);
  const currentSession = useRef(sessionId);
  const epoch = useRef(0);
  const operation = useRef<AbortController | null>(null);
  const decision = useRef<AbortController | null>(null);
  const listener = useRef(onEvent); listener.current = onEvent;
  const [isApproving, setApproving] = useState(false);
  const clear = useCallback(() => {
    epoch.current++; operation.current?.abort(); decision.current?.abort();
    operation.current = null; decision.current = null;
    currentSession.current = sessionId; setIdentity(sessionId);
    setMessages([]); setToolCalls([]); setTrace(null); setApproval(null);
    setLoading(false); setApproving(false); setError(null);
  }, [sessionId]);
  useEffect(() => {
    clear();
    return () => { epoch.current++; operation.current?.abort(); decision.current?.abort(); };
  }, [client, sessionId, targetAgent, clear]);
  const stop = useCallback(() => { operation.current?.abort(); }, []);
  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || operation.current) return;
    const controller = new AbortController(); operation.current = controller;
    const generation = epoch.current;
    const id = crypto.randomUUID();
    const timings = new Map<string, {start: number; end?: number}>();
    const valid = () => generation === epoch.current;
    const finish = (key?: string) => {
      for (const [id, time] of timings) if ((!key || key === id) && time.end === undefined) time.end = performance.now();
      setMessages(old => old.map(m => m.id !== id ? m : {...m, reasoning:m.reasoning.map(block => {
        const time = timings.get(block.id);
        return time?.end !== undefined ? {...block,complete:true,durationMs:time.end-time.start} : block;
      })}));
    };
    setLoading(true); setError(null); setToolCalls([]); setTrace(null); setApproval(null);
    setMessages(old => [...old,{id:crypto.randomUUID(),role:'user',content,reasoning:[]},{id,role:'assistant',content:'',reasoning:[]}]);
    try {
      await client.chat(content, { sessionId:currentSession.current, targetAgent, signal:controller.signal, onEvent:event => {
        if (!valid() || controller.signal.aborted) return;
        if (event.type === 'session') { currentSession.current = event.data.session_id; setIdentity(event.data.session_id); }
        if (event.type === 'token' && event.data.delta) {
          finish('main'); setMessages(old => old.map(m => m.id === id ? {...m,content:m.content+event.data.delta} : m));
        }
        if ((event.type === 'reasoning' || event.type === 'agent_reasoning') && event.data.delta) {
          const agent = event.type === 'agent_reasoning' ? event.data.agent_name : undefined;
          const key = event.type === 'agent_reasoning' ? `agent:${agent}:${event.data.parent_call_id ?? ''}` : 'main';
          const time = timings.get(key) ?? {start:performance.now()}; time.end = undefined; timings.set(key,time);
          setMessages(old => old.map(m => {
            if (m.id !== id) return m;
            const block = m.reasoning.find(b => b.id === key);
            const next = {id:key,agent,content:(block?.content ?? '')+event.data.delta,durationMs:performance.now()-time.start,complete:false};
            return {...m,reasoning:block ? m.reasoning.map(b => b.id === key ? next : b) : [...m.reasoning,next]};
          }));
        }
        if (event.type === 'agent_token' && event.data.delta) finish(`agent:${event.data.agent_name}:${event.data.parent_call_id ?? ''}`);
        if (event.type === 'tool_call') setToolCalls(old => [...old,event.data]);
        if (event.type === 'tool_result') { setToolCalls(old => old.map(c => c.call_id === event.data.call_id ? event.data : c)); setApproval(null); }
        if (event.type === 'approval_required') setApproval(event.data);
        if (event.type === 'done') { setTrace(event.data.trace); setApproval(null); }
        if (event.type === 'error') { setError(new Error(event.data.message)); if (event.data.terminal || event.data.code === 'approval_timeout') setApproval(null); }
        listener.current?.(event);
      }});
    } catch (cause) {
      if (valid()) setError(controller.signal.aborted ? new Error('本轮已停止。已执行的业务操作不会自动撤销。') : cause instanceof Error ? cause : new Error(String(cause)));
      throw cause;
    } finally {
      if (operation.current === controller) operation.current = null;
      if (valid()) { finish(); setLoading(false); setApproval(null); }
    }
  }, [client,targetAgent]);
  const approve = useCallback(async (approved: boolean) => {
    if (!approval || !currentSession.current || decision.current) return;
    const generation = epoch.current;
    const pending = approval;
    const controller = new AbortController(); decision.current = controller; setApproving(true);
    try {
      await client.submitApproval(currentSession.current,pending.approval_id,approved,controller.signal);
      if (generation === epoch.current) setApproval(old => old?.approval_id === pending.approval_id ? null : old);
    } catch (cause) {
      if (generation === epoch.current && !controller.signal.aborted) setError(cause instanceof Error ? cause : new Error(String(cause)));
      throw cause;
    } finally {
      if (decision.current === controller) decision.current = null;
      if (generation === epoch.current) setApproving(false);
    }
  }, [client,approval]);
  const thinking = messages.at(-1)?.reasoning.map(block => block.content).join('\n') || null;
  return {messages,thinking,toolCalls,trace,approval,isLoading,isApproving,error,sessionId:identity,sendMessage,stop,reset:clear,approve};
}
