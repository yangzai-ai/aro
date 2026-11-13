import assert from 'node:assert/strict';
import { test } from 'node:test';
import { AroClient, SSEParser, HTTPError, UnknownOutcomeError } from '../dist/index.js';
const done = 'event: done\ndata: {"session_id":"s","trace":null}\n\n';
const frame = (type,data) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
test('parser handles split CRLF, multiline JSON, comments, unknown events, UTF8 and all 13 types', async () => {
 const events=[]; const parser=new SSEParser(event=>events.push(event));
 const text=': ping\r\n\r\nevent: future\r\ndata: not-json\r\n\r\nevent: token\r\ndata: {\r\ndata: "delta":"阿洛"}\r\n\r\n';
 for (const char of text) parser.push(char);
 assert.deepEqual(events,[{type:'token',data:{delta:'阿洛'}}]);
 for (const type of ['reasoning','tool_call','tool_result','agent_start','agent_done','agent_failed','agent_token','agent_reasoning','session','done','error','approval_required']) {
  parser.push(frame(type,{delta:'text',session_id:'s',terminal:false,message:'test'}));
 }
 assert.equal(new Set(events.map(e=>e.type)).size,13);
 const bytes=new TextEncoder().encode(frame('token',{delta:'你好'})+done);
 const stream=new ReadableStream({start(c){for(const byte of bytes)c.enqueue(Uint8Array.of(byte));c.close();}});
 const received=[];
 await new AroClient({baseUrl:'',fetch:async()=>new Response(stream)}).chat('hi',{onEvent:e=>received.push(e)});
 assert.equal(received[0].data.delta,'你好');
});
test('chat creates via first POST; forwards target, auth, hooks; approval remains a single bound POST', async()=>{
 const calls=[]; let tokens='',results=0,tools=0;
 const client=new AroClient({baseUrl:'/api/',token:'secret',fetch:async(url,init)=>{
  calls.push({url,init});return new Response(frame('session',{session_id:'s'})+frame('token',{delta:'OK'})+frame('tool_call',{call_id:'1',tool_name:'query',args:{}})+frame('tool_result',{call_id:'1',success:true})+done);
 }});
 await client.chat('hello',{sessionId:null,targetAgent:'expert',onToken:t=>tokens+=t,onToolCall:()=>tools++,onToolResult:()=>results++});
 assert.deepEqual(JSON.parse(calls[0].init.body),{message:'hello',session_id:null,target_agent:'expert'});
 assert.equal(calls[0].url,'/api/chat'); assert.equal(calls[0].init.headers.Authorization,'Bearer secret');
 assert.equal(tokens,'OK');assert.equal(tools,1);assert.equal(results,1);
 await client.submitApproval('s','a',false);
 assert.deepEqual(JSON.parse(calls[1].init.body),{session_id:'s',approval_id:'a',approved:false});
});
test('network failure and truncated/malformed streams never replay POST and report unknown outcome',async()=>{
 for(const scenario of ['network','truncated','invalid']) {
  let calls=0,errors=0;
  const client=new AroClient({baseUrl:'',fetch:async()=>{calls++;if(scenario==='network')throw new TypeError('offline');return new Response(scenario==='invalid'?'event: token\ndata: bad-json\n\n':frame('session',{session_id:'s'}));}});
  await assert.rejects(client.chat(null,'write',{onError:()=>errors++}),UnknownOutcomeError);
  assert.equal(calls,1);assert.equal(errors,1);
 }
});
test('nonterminal error continues, terminal error ends without requiring done', async()=>{
 const events=[];
 const client=new AroClient({baseUrl:'',fetch:async()=>new Response(frame('error',{code:'approval_timeout',message:'timeout',terminal:false})+frame('token',{delta:'rejected'})+done)});
 await client.chat('hi',{onEvent:e=>events.push(e)});assert.equal(events.length,3);
 await new AroClient({baseUrl:'',fetch:async()=>new Response(frame('error',{code:'failed',message:'failed',terminal:true}))}).chat('hi');
});
test('GET exponential retry is bounded and sends session header; 401/404 and POST are not retried',async()=>{
 let calls=0;
 const client=new AroClient({baseUrl:'https://aro',retryDelayMs:0,fetch:async(url,init)=>{calls++;assert.equal(init.headers['X-ARO-Session'],'s');return calls<3?new Response('{}',{status:503}):Response.json({trace_id:'t'});}});
 assert.equal((await client.getTrace('t',{sessionId:'s'})).trace_id,'t');assert.equal(calls,3);
 for(const status of [401,403,404,429,503]) {
  let count=0;
  const failed=new AroClient({baseUrl:'',retryDelayMs:0,queryRetries:2,fetch:async()=>{count++;return Response.json({detail:'no'},{status});}});
  await assert.rejects(failed.listTraces('s'),HTTPError);assert.equal(count,[429,503].includes(status)?3:1);
  count=0;await assert.rejects(failed.chat('hi'),HTTPError);assert.equal(count,1);
  count=0;await assert.rejects(failed.submitApproval('s','a',true),HTTPError);assert.equal(count,1);
 }
});
test('abort during GET backoff cancels promptly without retry; callback error is preserved',async()=>{
 const controller=new AbortController();let count=0;
 const client=new AroClient({baseUrl:'',retryDelayMs:1000,fetch:async()=>{count++;setTimeout(()=>controller.abort(),5);return new Response('{}',{status:503});}});
 await assert.rejects(client.listTraces('s',{signal:controller.signal}),e=>e.name==='AbortError');assert.equal(count,1);
 const failure=new Error('host failed');
 await assert.rejects(new AroClient({baseUrl:'',fetch:async()=>new Response(done)}).chat('hi',{onEvent:()=>{throw failure;}}),e=>e===failure);
});
test('stream cancellation releases reader; no signal is internally replaced',async()=>{
 const controller=new AbortController();let cancelled=false;
 const client=new AroClient({baseUrl:'',fetch:async(_,init)=>{
  assert.equal(init.signal,controller.signal);
  return new Response(new ReadableStream({start(c){init.signal.addEventListener('abort',()=>c.error(init.signal.reason));c.enqueue(new TextEncoder().encode(frame('session',{session_id:'s'})));},cancel(){cancelled=true;}}));
 }});
 const pending=client.chat('hi',{signal:controller.signal,onEvent:()=>controller.abort()});
 await assert.rejects(pending,e=>e.name==='AbortError');
 // An errored stream already releases its source; it need not call cancel().
 assert.equal(controller.signal.aborted,true);
});

test('CR-only terminal frames and transport failure after done retain known outcome',async()=>{
 await new AroClient({baseUrl:'',fetch:async()=>new Response(done.replaceAll('\n','\r'))}).chat('hi');
 let pulls=0;
 await new AroClient({baseUrl:'',fetch:async()=>new Response(new ReadableStream({pull(c){if(pulls++===0)c.enqueue(new TextEncoder().encode(done));else c.error(new TypeError('after terminal'));}}))}).chat('hi');
});
