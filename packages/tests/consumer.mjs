import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {AroClient} from '@aro/client';
import {ChatMessage,ThinkingBlock,ToolCallCard,TracePanel,ApprovalCard,useAroChat} from '@aro/react';
assert.ok(fileURLToPath(import.meta.resolve('@aro/client')).startsWith(process.cwd()));
assert.ok(fileURLToPath(import.meta.resolve('@aro/react')).startsWith(process.cwd()));
let session=null,text='',count=0;
const trace={trace_id:'t',thread_id:'s',user_input:'hello',agent_response:'ok',tool_calls:[],duration_ms:1,error:null};
const client=new AroClient({baseUrl:'/api',fetch:async(url,init)=>{
 count++;
 if(init.method==='GET'){assert.equal(init.headers['X-ARO-Session'],'s');return Response.json(trace);}
 return new Response('event: session\ndata: {"session_id":"s"}\n\nevent: token\ndata: {"delta":"ok"}\n\nevent: done\ndata: {"session_id":"s","trace":null}\n\n');
}});
await client.chat('hello',{onEvent:e=>{if(e.type==='session')session=e.data.session_id;},onToken:delta=>text+=delta});
assert.equal(session,'s');assert.equal(text,'ok');assert.equal((await client.getTrace('t',{sessionId:session})).trace_id,'t');
function Host(){const state=useAroChat({client});return React.createElement('div',null,
 React.createElement(ChatMessage,{role:'assistant',content:'<script>alert(1)</script>'}),
 React.createElement(ThinkingBlock,{content:'thinking'}),
 React.createElement(ToolCallCard,{step:{call_id:'c',tool_name:'query',args:{},success:true,result:'done'}}),
 React.createElement(TracePanel,{trace}),
 React.createElement(ApprovalCard,{approval:{approval_id:'a',tool_name:'write',args:{},expires_at:9999999999},sending:state.isApproving,onDecision:()=>{}}));}
const html=renderToString(React.createElement(Host));assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('query'));assert.equal(count,2);
console.log('isolated tarball consumer: SDK HTTP/SSE, React render, all five components and Hook passed');
