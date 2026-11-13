import type { AroEvent } from './types.js';

export class UnknownOutcomeError extends Error {
  constructor() { super('连接已中断，本轮未完成，执行结果未知。请检查执行记录后重新提问。'); this.name = 'UnknownOutcomeError'; }
}

const known = new Set(['token','reasoning','tool_call','tool_result','agent_start','agent_done','agent_failed','agent_token','agent_reasoning','session','done','error','approval_required']);

/** Incremental SSE line parser; handles CR, LF, CRLF split across chunks and multiline data. */
export class SSEParser {
  private buffer = '';
  private type = '';
  private data: string[] = [];
  constructor(private readonly receive: (event: AroEvent) => void) {}
  push(chunk: string): void {
    this.buffer += chunk;
    let index: number;
    while ((index = this.buffer.search(/[\r\n]/)) >= 0) {
      const end = this.buffer[index];
      if (end === '\r' && index === this.buffer.length - 1) return;
      const line = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + (end === '\r' && this.buffer[index + 1] === '\n' ? 2 : 1));
      this.line(line);
    }
  }
  end(): void {
    // A final CR is a line ending, not an unfinished CRLF. Do not dispatch an
    // event without its blank-line terminator just because the stream ended.
    if (this.buffer.endsWith('\r')) this.push('\n');
  }
  private line(line: string): void {
    if (!line) {
      if (this.data.length && known.has(this.type)) {
        const data: unknown = JSON.parse(this.data.join('\n'));
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid SSE event data');
        const value = data as Record<string, unknown>;
        if (['token','reasoning','agent_token','agent_reasoning'].includes(this.type) && typeof value.delta !== 'string') throw new Error('Invalid SSE delta');
        if (['session','done'].includes(this.type) && typeof value.session_id !== 'string') throw new Error('Invalid SSE session');
        if (this.type === 'error' && (typeof value.terminal !== 'boolean' || typeof value.message !== 'string')) throw new Error('Invalid SSE error');
        this.receive({ type: this.type, data } as AroEvent);
      }
      this.type = ''; this.data = []; return;
    }
    if (line.startsWith(':')) return;
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
    if (field === 'event') this.type = value;
    if (field === 'data') this.data.push(value);
    // No id/retry semantics: this server cannot resume a chat.
  }
}

export async function readEvents(response: Response, receive: (event: AroEvent) => void): Promise<void> {
  if (!response.body) throw new UnknownOutcomeError();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let finished = false;
  const parser = new SSEParser(event => {
    receive(event);
    if (event.type === 'done' || (event.type === 'error' && event.data.terminal)) finished = true;
  });
  try {
    while (true) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try { chunk = await reader.read(); }
      catch (error) {
        // A transport failure after a terminal frame cannot undo that frame.
        if (finished) break;
        throw error;
      }
      const { done, value } = chunk;
      if (done) { parser.push(decoder.decode()); parser.end(); break; }
      parser.push(decoder.decode(value, { stream: true }));
    }
    if (!finished) throw new UnknownOutcomeError();
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
