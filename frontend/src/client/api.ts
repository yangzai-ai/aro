import type { AgentEvent } from './types';

function headers(token: string): Record<string, string> {
  return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

export async function projectRequest(path: string, session: string, token: string, method = 'GET', signal?: AbortSignal): Promise<Response> {
  return checked(await fetch(`/api/helpdesk/${path}`, { method, headers: { ...headers(token), 'X-ARO-Session': session }, signal }));
}

export class HTTPError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
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
  onEvent: (event: AgentEvent) => void, signal: AbortSignal,
): Promise<void> {
  const response = await checked(await fetch('/api/chat', {
    method: 'POST', headers: headers(token),
    body: JSON.stringify({ message, session_id: sessionId }), signal,
  }));
  if (!response.body) throw new Error('浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', finished = false;
  const consume = () => {
    // A boundary may be split across arbitrary network chunks.
    let match: RegExpExecArray | null;
    while ((match = /\r?\n\r?\n/.exec(buffer))) {
      const frame = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      let type = 'message';
      const data: string[] = [];
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith('event:')) type = line.slice(6).trim();
        if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (!data.length) continue;
      const event = { type, data: JSON.parse(data.join('\n')) } as AgentEvent;
      if (type === 'done' || (event.type === 'error' && event.data.terminal)) finished = true;
      onEvent(event);
    }
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) { buffer += decoder.decode(); consume(); break; }
      buffer += decoder.decode(value, { stream: true });
      consume();
    }
    if (!finished) throw new Error('连接已中断，本轮未完成。请检查执行记录后重新提问。');
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export async function submitApproval(sessionId: string, approvalId: string, approved: boolean, token: string): Promise<void> {
  await checked(await fetch('/api/approve', {
    method: 'POST', headers: headers(token),
    body: JSON.stringify({ session_id: sessionId, approval_id: approvalId, approved }),
  }));
}
