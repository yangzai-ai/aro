import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../package.json',import.meta.url));
const {build}=require('esbuild');
const React=require('react');
const TestRenderer=require('react-test-renderer');const {act}=TestRenderer;
const compiled=await build({entryPoints:['dist/index.js'],bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/*']});
const mod={exports:{}};new Function('module','exports','require',compiled.outputFiles[0].text)(mod,mod.exports,require);
const {ChatMessage,ThinkingBlock,ToolCallCard,TracePanel,ApprovalCard,useAroChat}=mod.exports;
const h=React.createElement;
test('five components render real evidence; plain content is escaped, thinking absent if empty',()=>{
 let view;act(()=>{view=TestRenderer.create(h(ChatMessage,{role:'assistant',content:'<script>bad</script>'}));});
 assert.equal(view.root.findAllByType('details').length,0);assert.ok(JSON.stringify(view.toJSON()).includes('<script>bad</script>'));
 act(()=>view.update(h(ThinkingBlock,{content:'reason',active:true,agent:'expert',durationMs:1000})));
 assert.equal(view.root.findByType('details').props.open,true);
 act(()=>view.update(h(ThinkingBlock,{content:'reason',active:false,agent:'expert'})));
 assert.equal(view.root.findByType('details').props.open,false);
 act(()=>view.root.findByType('details').props.onToggle({currentTarget:{open:true}}));
 assert.equal(view.root.findByType('details').props.open,true);
 const step={call_id:'c',tool_name:'query',args:{id:1},success:true,result:{found:1},duration_ms:3,execution_duration_ms:2,approval_duration_ms:1,hook_errors:[]};
 act(()=>view.update(h(TracePanel,{trace:{trace_id:'t',duration_ms:3,tool_calls:[step]}})));
 assert.equal(view.root.findAllByType(ToolCallCard).length,1);assert.ok(JSON.stringify(view.toJSON()).includes('found'));
 let chosen;act(()=>view.update(h(ApprovalCard,{approval:{approval_id:'a',tool_name:'write',args:{},expires_at:Date.now()/1000+60},sending:false,onDecision:v=>chosen=v})));
 act(()=>view.root.findAllByType('button')[1].props.onClick());assert.equal(chosen,true);
 act(()=>view.update(h(ApprovalCard,{approval:{approval_id:'a',tool_name:'write',args:{},expires_at:1},sending:false,onDecision:()=>{}})));
 assert.ok(view.root.findAllByType('button').every(b=>b.props.disabled));act(()=>view.unmount());
});
async function mounted(client,run) {
 let api,view;const Harness=props=>{api=useAroChat(props);return null;};
 await act(async()=>{view=TestRenderer.create(h(Harness,{client}));});
 try{await run(()=>api,props=>act(()=>view.update(h(Harness,{client,...props}))));}finally{act(()=>view.unmount());}
}
test('hook first chat creates session, second reuses; reasoning and child prose do not enter content',async()=>{
 const identities=[];
 const client={async chat(message,options){identities.push(options.sessionId);options.onEvent({type:'session',data:{session_id:'s'}});options.onEvent({type:'reasoning',data:{delta:'private'}});options.onEvent({type:'agent_reasoning',data:{delta:'child',agent_name:'expert',parent_call_id:'c'}});options.onEvent({type:'agent_token',data:{delta:'not main',agent_name:'expert',parent_call_id:'c'}});options.onEvent({type:'token',data:{delta:'answer'}});options.onEvent({type:'done',data:{session_id:'s',trace:null}});}};
 await mounted(client,async api=>{
  await act(async()=>{await api().sendMessage('hi');});assert.equal(api().messages[1].content,'answer');assert.equal(api().messages[1].reasoning.length,2);assert.ok(api().messages[1].reasoning.every(b=>b.complete));
  await act(async()=>{await api().sendMessage('again');});assert.deepEqual(identities,[null,'s']);assert.equal(api().isLoading,false);
 });
});
test('hook switch session/target ignores late stream and aborts; reset and unmount release work',async()=>{
 let send,finish,signal;
 const client={chat(_,options){send=options.onEvent;signal=options.signal;return new Promise(resolve=>{finish=resolve;});}};
 await mounted(client,async(api,update)=>{
  let pending;act(()=>{pending=api().sendMessage('hi');});assert.equal(api().isLoading,true);
  update({sessionId:'other',targetAgent:'expert'});assert.equal(signal.aborted,true);
  await act(async()=>{send({type:'token',data:{delta:'late'}});finish();await pending;});
  assert.equal(api().messages.length,0);assert.equal(api().sessionId,'other');assert.equal(api().isLoading,false);
  act(()=>{pending=api().sendMessage('new');});act(()=>api().reset());assert.equal(signal.aborted,true);
  await act(async()=>{finish();await pending;});assert.equal(api().messages.length,0);
 });
});
test('hook approvals use actual session/id and do not clear newer pending approval',async()=>{
 let send,finish,resolveDecision,args;
 const client={chat(_,options){send=options.onEvent;return new Promise(resolve=>{finish=resolve;});},submitApproval(...values){args=values;return new Promise(resolve=>resolveDecision=resolve);}};
 await mounted(client,async api=>{
  let pending,decision;act(()=>{pending=api().sendMessage('write');});
  act(()=>{send({type:'session',data:{session_id:'s'}});send({type:'approval_required',data:{approval_id:'a',tool_name:'write',args:{},expires_at:9999999999}});});
  act(()=>{decision=api().approve(true);});assert.deepEqual(args.slice(0,3),['s','a',true]);
  act(()=>send({type:'approval_required',data:{approval_id:'b',tool_name:'next',args:{},expires_at:9999999999}}));
  await act(async()=>{resolveDecision();await decision;});assert.equal(api().approval.approval_id,'b');
  await act(async()=>{finish();await pending;});
 });
});
test('hook errors settle loading; stop aborts without rollback claim',async()=>{
 const client={async chat(_,options){options.signal.throwIfAborted();throw new Error('unknown');}};
 await mounted(client,async api=>{await act(async()=>{await assert.rejects(api().sendMessage('hi'));});assert.equal(api().isLoading,false);assert.equal(api().error.message,'unknown');});
});

test('hook stop aborts pending request, prevents double send, and reports no rollback promise',async()=>{
 let calls=0,signal;
 const client={chat(_,options){calls++;signal=options.signal;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)));}};
 await mounted(client,async api=>{
  let pending;act(()=>{pending=api().sendMessage('write');});
  await act(async()=>{await api().sendMessage('double');});assert.equal(calls,1);
  await act(async()=>{api().stop();await assert.rejects(pending);});
  assert.equal(signal.aborted,true);assert.equal(api().isLoading,false);assert.match(api().error.message,/不会自动撤销/);
 });
});
test('hook unmount cancels pending stream and approval resources',async()=>{
 let api,view,streamSignal,decisionSignal,finish;
 const client={chat(_,options){streamSignal=options.signal;options.onEvent({type:'session',data:{session_id:'s'}});options.onEvent({type:'approval_required',data:{approval_id:'a',tool_name:'write',args:{},expires_at:9999999999}});return new Promise(resolve=>finish=resolve);},submitApproval(_,__,___,signal){decisionSignal=signal;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason)));}};
 const Harness=()=>{api=useAroChat({client});return null;};
 act(()=>{view=TestRenderer.create(h(Harness));});let pending,approval;
 act(()=>{pending=api.sendMessage('hi');});act(()=>{approval=api.approve(true).catch(e=>e);});
 act(()=>view.unmount());assert.equal(streamSignal.aborted,true);assert.equal(decisionSignal.aborted,true);
 await act(async()=>{finish();await pending;await approval;});
});
