import { AroClient, HTTPError } from '@aro/client';
export { HTTPError } from '@aro/client';
import type { AgentEvent } from './types';

function headers(token: string): Record<string, string> {
  return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

export async function projectRequest(path: string, session: string, token: string, method = 'GET', signal?: AbortSignal): Promise<Response> {
  return checked(await fetch(`/api/helpdesk/${path}`, { method, headers: { ...headers(token), 'X-ARO-Session': session }, signal }));
}

async function checked(response: Response): Promise<Response> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new HTTPError(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`, response.status);
  }
  return response;
}

/** POST streaming with UTF-8 boundaries, CRLF and multiline data frames handled. */
export async function streamChat(
  message: string, sessionId: string | null, token: string,
  onEvent: (event: AgentEvent) => void, signal: AbortSignal, targetAgent: string | null = null,
): Promise<void> {
  await new AroClient({ baseUrl: '/api', token }).chat(message, {
    sessionId, targetAgent, onEvent, signal,
  });
}

export async function submitApproval(sessionId: string, approvalId: string, approved: boolean, token: string): Promise<void> {
  await new AroClient({ baseUrl: '/api', token }).submitApproval(sessionId, approvalId, approved);
}
