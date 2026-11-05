import type { ChatMessage } from '../client/types';
import type { Preset } from '../theme/theme';

export interface Conversation {
  id: string; sessionId: string | null; title: string; updated: number;
  targetAgent?: string | null;
  preset: Preset; messages: ChatMessage[]; closed: boolean;
}
export function readHistory(): { records: Conversation[]; available: boolean } {
  try {
    const data: unknown = JSON.parse(localStorage.getItem('aro.history') || '[]');
    if (!Array.isArray(data)) throw new Error('Invalid history');
    const records = data.filter((item): item is Conversation => item && typeof item.id === 'string' &&
      (item.sessionId === null || typeof item.sessionId === 'string') && typeof item.title === 'string' &&
      (item.targetAgent === undefined || item.targetAgent === null || typeof item.targetAgent === 'string') && typeof item.updated === 'number' && ['it', 'service', 'general'].includes(item.preset) && typeof item.closed === 'boolean' &&
      Array.isArray(item.messages) && item.messages.every((m: Partial<ChatMessage>) => m && typeof m.id === 'string' && typeof m.text === 'string' && ['user', 'assistant'].includes(m.role || '') &&
        (m.reasoning_blocks === undefined || (Array.isArray(m.reasoning_blocks) && m.reasoning_blocks.every(block =>
          block && typeof block.id === 'string' && typeof block.text === 'string' &&
          (block.agent === undefined || typeof block.agent === 'string') &&
          typeof block.duration_ms === 'number' && Number.isFinite(block.duration_ms) && block.duration_ms >= 0 && typeof block.complete === 'boolean')))));
    return { records, available: true };
  } catch { return { records: [], available: false }; }
}
export function persistHistory(records: Conversation[]): boolean {
  try { localStorage.setItem('aro.history', JSON.stringify(records)); return true; } catch { return false; }
}
export function sessionValue(key: string, value?: string | null): string | null {
  try {
    if (value === undefined) return sessionStorage.getItem(key);
    if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value);
  } catch { /* Storage can be unavailable; in-memory conversation still works. */ }
  return null;
}
