import { readEvents, UnknownOutcomeError } from './sse.js';
import type { AgentTrace, AroEvent, ToolCallEvent, ToolResultEvent } from './types.js';

export class HTTPError extends Error {
  constructor(message: string, public readonly status: number) { super(message); this.name = 'HTTPError'; }
}
export interface ChatOptions {
  sessionId?: string | null; targetAgent?: string | null; signal?: AbortSignal;
  onEvent?: (event: AroEvent) => void; onToken?: (delta: string) => void;
  onToolCall?: (call: ToolCallEvent) => void; onToolResult?: (result: ToolResultEvent) => void;
  onError?: (error: Error) => void;
}
export interface ClientOptions {
  baseUrl: string; token?: string; fetch?: typeof fetch;
  /** GET retries only (0..5); chat/approve are never retried. */
  queryRetries?: number; retryDelayMs?: number;
}
function abortableDelay(ms: number, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); reject(signal?.reason); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', cancel); resolve(); }, ms);
    signal?.addEventListener('abort', cancel, { once: true });
  });
}
export class AroClient {
  private readonly retries: number;
  private readonly delay: number;
  constructor(private readonly options: ClientOptions) {
    this.retries = options.queryRetries ?? 2; this.delay = options.retryDelayMs ?? 250;
    if (!Number.isInteger(this.retries) || this.retries < 0 || this.retries > 5 || !Number.isFinite(this.delay) || this.delay < 0 || this.delay > 10000) throw new Error('Invalid GET retry settings');
  }
  private async request(path: string, init: RequestInit, retry = false): Promise<Response> {
    const headers = { 'Content-Type': 'application/json', ...(this.options.token ? { Authorization: `Bearer ${this.options.token}` } : {}), ...init.headers };
    for (let attempt = 0; ; attempt++) {
      init.signal?.throwIfAborted();
      try {
        const response = await (this.options.fetch ?? globalThis.fetch)(this.options.baseUrl.replace(/\/$/, '') + path, { ...init, headers });
        if (response.ok) return response;
        const body = await response.json().catch(() => ({}));
        throw new HTTPError(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`, response.status);
      } catch (error) {
        if (init.signal?.aborted || !retry || attempt >= this.retries || (error instanceof HTTPError ? ![429,502,503,504].includes(error.status) : !(error instanceof TypeError))) throw error;
        await abortableDelay(this.delay * 2 ** attempt, init.signal ?? undefined);
      }
    }
  }
  async chat(message: string, options?: ChatOptions): Promise<void>;
  async chat(sessionId: string | null, message: string, options?: ChatOptions): Promise<void>;
  async chat(first: string | null, second: string | ChatOptions = {}, third: ChatOptions = {}): Promise<void> {
    const options = typeof second === 'string' ? { ...third, sessionId: first } : second;
    const message = typeof second === 'string' ? second : first;
    let callbackError = false;
    try {
      const response = await this.request('/chat', { method: 'POST', signal: options.signal, body: JSON.stringify({message, session_id: options.sessionId ?? null, target_agent: options.targetAgent ?? null}) });
      await readEvents(response, event => {
        try {
          options.onEvent?.(event);
          if (event.type === 'token') options.onToken?.(event.data.delta);
          if (event.type === 'tool_call') options.onToolCall?.(event.data);
          if (event.type === 'tool_result') options.onToolResult?.(event.data);
        } catch (error) { callbackError = true; throw error; }
      });
    } catch (error) {
      const failure = options.signal?.aborted || callbackError || error instanceof HTTPError || error instanceof UnknownOutcomeError ? error : new UnknownOutcomeError();
      options.onError?.(failure instanceof Error ? failure : new Error(String(failure)));
      throw failure;
    }
  }
  async submitApproval(sessionId: string, approvalId: string, approved: boolean, signal?: AbortSignal): Promise<void> {
    await this.request('/approve', {method:'POST', signal, body:JSON.stringify({session_id:sessionId,approval_id:approvalId,approved})});
  }
  async getTrace(traceId: string, options: {sessionId: string; signal?: AbortSignal}): Promise<AgentTrace> {
    return (await this.request(`/traces/${encodeURIComponent(traceId)}`, {method:'GET',headers:{'X-ARO-Session':options.sessionId},signal:options.signal}, true)).json();
  }
  async listTraces(sessionId: string, options: {limit?: number; offset?: number; signal?: AbortSignal} = {}): Promise<AgentTrace[]> {
    const query = new URLSearchParams({limit:String(options.limit ?? 50),offset:String(options.offset ?? 0)});
    return (await this.request(`/sessions/${encodeURIComponent(sessionId)}/traces?${query}`, {method:'GET',signal:options.signal}, true)).json();
  }
}
