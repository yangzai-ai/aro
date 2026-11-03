import assert from 'node:assert/strict';
import { test } from 'node:test';
import { build } from 'esbuild';

// Vite already uses esbuild; no test dependency or browser runtime is added.
async function moduleAt(entry) {
  const result = await build({ entryPoints: [entry], bundle: true, write: false, platform: 'node', format: 'esm', jsx: 'automatic' });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}
const { isPanel } = await moduleAt('src/components/TicketPanel.tsx');
const { readHistory, persistHistory, sessionValue } = await moduleAt('src/history/storage.ts');
const { streamChat, submitApproval, projectRequest, HTTPError } = await moduleAt('src/client/api.ts');
const voiceBuild = await build({ stdin: { contents: `import { createElement } from 'react'; import { renderToString } from 'react-dom/server'; import { VoiceButton } from './src/components/VoiceButton'; export const voiceMarkup = disabled => renderToString(createElement(VoiceButton, {disabled, onText: () => {}}));`, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic' });
// CJS output keeps React and its server renderer on the same hook dispatcher.
const { createRequire } = await import('node:module');
const voiceModule = { exports: {} };
new Function('module', 'exports', 'require', voiceBuild.outputFiles[0].text)(voiceModule, voiceModule.exports, createRequire(import.meta.url));
const { voiceMarkup } = voiceModule.exports;
const ticket = { id: 'TKT-001', title: '支持请求', description: '事实', category: 'account', priority: 'high', status: 'open', requester: '测试用户', assignee: null, created_at: '2026-11-03T00:00:00Z', updated_at: '2026-11-03T00:00:00Z', resolved_at: null };
const panel = { source: 'mock', observed_at: ticket.created_at, tickets: [ticket], counts: {open:1,assigned:0,in_progress:0,resolved:0,closed:0}, selected: ticket, history: [], duplicate_check: null, artifacts: [] };
test('panel accepts actual fields, history, duplicate evidence and safe artifact metadata', () => {
  assert.equal(isPanel(panel), true);
  assert.equal(isPanel({...panel, history:[{ticket_id:'TKT-001',timestamp:ticket.created_at,action:'update',operator:'support',details:{status:'open'}}], duplicate_check:{observed_at:ticket.created_at,result:{query:{title:ticket.title,category:ticket.category},duplicates:[ticket]}},artifacts:[{id:'a'.repeat(48),filename:'report-12345678.docx',expires_at:ticket.created_at,size:100}]}),true);
});
test('panel rejects incomplete, invalid-status and malformed evidence data', () => {
  for (const invalid of [null, {}, {...panel, tickets:[{...ticket,status:'unknown'}]}, {...panel, counts:{open:-1}}, {...panel, observed_at:'bad-date'}, {...panel,duplicate_check:{result:{duplicates:[]}}}, {...panel,history:[{}]}]) assert.equal(isPanel(invalid),false);
});
test('panel rejects unsafe download metadata', () => {
  for (const filename of ['../../private.docx','https://invalid/report.docx','report.docx']) assert.equal(isPanel({...panel,artifacts:[{id:'a'.repeat(48),filename,expires_at:ticket.created_at,size:100}]}),false);
  assert.equal(isPanel({...panel,artifacts:[{id:'bad',filename:'report-12345678.docx',expires_at:ticket.created_at,size:100}]}),false);
});
test('history and session storage fail safely without blocking current conversation', () => {
  const broken = { getItem() { throw new Error('disabled'); }, setItem() { throw new Error('quota'); }, removeItem() { throw new Error('disabled'); } };
  globalThis.localStorage = broken; globalThis.sessionStorage = broken;
  assert.deepEqual(readHistory(),{records:[],available:false});
  assert.equal(persistHistory([]),false);
  assert.equal(sessionValue('aro.active'),null);
  assert.equal(sessionValue('aro.active','id'),null);
  assert.equal(sessionValue('aro.active',null),null);
});
test('local transcripts round-trip, malformed records are excluded, corrupt JSON is bounded', () => {
  const values = new Map();
  globalThis.localStorage = {getItem:key => values.get(key) || null, setItem:(key,value) => values.set(key,value)};
  const record = {id:'local',sessionId:'opaque',title:'支持请求',updated:1,preset:'it',messages:[{id:'m',role:'user',text:'支持请求'}],closed:true};
  assert.equal(persistHistory([record]),true); assert.deepEqual(readHistory().records,[record]);
  values.set('aro.history',JSON.stringify([record,{id:'bad',messages:[{role:'system'}]}]));
  assert.deepEqual(readHistory().records,[record]);
  values.set('aro.history','{'); assert.deepEqual(readHistory(),{records:[],available:false});
});
test('SSE preserves UTF-8, CRLF, multiline frames and completion', async () => {
  const source = new TextEncoder().encode('event: token\r\ndata: {"delta":\r\ndata: "中文"}\r\n\r\nevent: done\ndata: {"session_id":"s","trace":null}\n\n');
  const original = globalThis.fetch;
  const events = [];
  globalThis.fetch = async (_url, init) => {
    assert.equal(JSON.parse(init.body).session_id, 's');
    assert.equal(init.headers.Authorization,'Bearer test-token');
    return new Response(new ReadableStream({ start(controller) { for (const byte of source) controller.enqueue(Uint8Array.of(byte)); controller.close(); } }));
  };
  try { await streamChat('hello','s','test-token',event => events.push(event),new AbortController().signal); }
  finally { globalThis.fetch = original; }
  assert.deepEqual(events.map(event => event.type),['token','done']);
  assert.equal(events[0].data.delta,'中文');
});
test('incomplete SSE fails explicitly, HTTP failure retains status for expiry checks', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response('event: token\ndata: {"delta":"partial"}\n\n');
    await assert.rejects(streamChat('hello',null,'',() => {},new AbortController().signal),/连接已中断/);
    globalThis.fetch = async () => new Response(JSON.stringify({detail:'expired'}),{status:404});
    await assert.rejects(projectRequest('panel','s',''),error => error instanceof HTTPError && error.status === 404 && error.message === 'expired');
  } finally { globalThis.fetch = original; }
});
test('approval stays bound to session and approval ID; project download carries session/auth', async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url,init) => { requests.push({url,...init}); return new Response('{}',{status:202}); };
  try {
    await submitApproval('session','approval',false,'test-token');
    await projectRequest('artifacts/' + 'a'.repeat(48),'session','test-token');
    assert.deepEqual(JSON.parse(requests[0].body),{session_id:'session',approval_id:'approval',approved:false});
    assert.equal(requests[0].headers.Authorization,'Bearer test-token');
    assert.equal(requests[1].headers['X-ARO-Session'],'session');
    assert.equal(requests[1].headers.Authorization,'Bearer test-token');
  } finally { globalThis.fetch = original; }
});
test('voice control is absent without Web Speech support and disabled while busy', () => {
  globalThis.window = {};
  assert.equal(voiceMarkup(false),'');
  globalThis.window = {webkitSpeechRecognition:class {}};
  assert.match(voiceMarkup(false),/开始语音输入/);
  assert.match(voiceMarkup(true),/disabled/);
  delete globalThis.window;
});
