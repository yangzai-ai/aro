import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { build } from 'esbuild';

// Execute the real App and its children with React's state/effect machinery.
// Browser globals and HTTP are isolated; click handlers and state are not mocked.
const compiled = await build({entryPoints:['src/App.tsx'],bundle:true,write:false,
  platform:'node',format:'cjs',jsx:'automatic',external:['react','react/*']});
const appModule = {exports:{}};
new Function('module','exports','require',compiled.outputFiles[0].text)(appModule,appModule.exports,createRequire(import.meta.url));
const App = appModule.exports.default;
const experts = [
  {name:'ticket_analyst',description:'只读工单分析'},
  {name:'knowledge_expert',description:'只读知识检索'},
];

async function mounted(run, chatHandler) {
  const originals = Object.fromEntries(['window','document','localStorage','sessionStorage','fetch'].map(key => [key,globalThis[key]]));
  const values = new Map(), sessions = new Map(), requests = [];
  globalThis.localStorage = {getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value)};
  globalThis.sessionStorage = {getItem:key=>sessions.get(key) || null,setItem:(key,value)=>sessions.set(key,value),removeItem:key=>sessions.delete(key)};
  globalThis.window = {innerWidth:1280,addEventListener(){},removeEventListener(){}};
  globalThis.document = {documentElement:{dataset:{theme:'light',preset:'it'}},getElementById(){return null;},querySelector(){return null;}};
  globalThis.fetch = async (url, init={}) => {
    if (url === '/api/helpdesk/agents') return Response.json(experts);
    if (url === '/api/chat') {
      const body = JSON.parse(init.body); requests.push(body);
      if (chatHandler) return chatHandler(body);
      const identity = body.session_id || `session-${body.target_agent || 'main'}`;
      return new Response(`event: session\ndata: ${JSON.stringify({session_id:identity})}\n\nevent: token\ndata: {"delta":"已回复"}\n\nevent: done\ndata: ${JSON.stringify({session_id:identity,trace:null})}\n\n`);
    }
    if (url.startsWith('/api/helpdesk/panel')) return Response.json({source:'mock',observed_at:'2026-11-04T00:00:00Z',tickets:[],counts:{open:0,assigned:0,in_progress:0,resolved:0,closed:0},selected:null,history:[],duplicate_check:null,artifacts:[]});
    return Response.json({status:'ok'});
  };
  let renderer;
  try {
    await act(async()=>{renderer=TestRenderer.create(React.createElement(App));});
    await run(renderer.root,requests,values);
  } finally {
    if(renderer) await act(async()=>renderer.unmount());
    for(const [key,value] of Object.entries(originals)) {
      if(value===undefined) delete globalThis[key]; else globalThis[key]=value;
    }
  }
}

function text(node) { return node.children.map(child=>typeof child==='string'?child:text(child)).join(''); }
function button(root,label) { return root.findAllByType('button').find(node=>text(node)===label); }
async function click(node) { assert.ok(node,'button exists'); await act(async()=>{node.props.onClick();}); }
async function send(root,message) {
  await act(async()=>{root.findByType('textarea').props.onChange({target:{value:message}});});
  await act(async()=>{root.findByType('form').props.onSubmit({preventDefault(){}});});
}
function selected(root,name) {
  assert.equal(button(root,name).props['aria-pressed'],true);
  assert.match(root.findByType('textarea').props.placeholder,new RegExp(name));
}

test('expert card selects the actual target, returns to history and closes navigation', async()=>{
  await mounted(async(root,requests)=>{
    await click(root.findByProps({id:'experts-tab'}));
    const card=root.findAll(node=>node.type==='article' && node.props.className==='expert-card')[0];
    await click(card.findByType('button'));
    selected(root,'ticket_analyst');
    assert.equal(root.findByProps({id:'history-tab'}).props['aria-selected'],true);
    assert.equal(root.findByProps({id:'navigation'}).props.hidden,true);
    await send(root,'查询工单');
    assert.equal(requests[0].target_agent,'ticket_analyst');
    assert.equal(requests[0].session_id,null);
  });
});

test('composer chips keep independent expert sessions and restore the main target', async()=>{
  await mounted(async(root,requests)=>{
    await click(button(root,'ticket_analyst'));
    selected(root,'ticket_analyst');
    await send(root,'第一轮');
    await send(root,'第二轮');
    await click(button(root,'knowledge_expert'));
    selected(root,'knowledge_expert');
    await send(root,'知识问题');
    await click(button(root,'总控助手'));
    assert.equal(button(root,'总控助手').props['aria-pressed'],true);
    await send(root,'总控问题');
    await click(button(root,'ticket_analyst'));
    selected(root,'ticket_analyst');
    await send(root,'恢复工单会话');
    assert.deepEqual(requests.map(r=>[r.target_agent,r.session_id]),[
      ['ticket_analyst',null],['ticket_analyst','session-ticket_analyst'],
      ['knowledge_expert',null],[null,null],['ticket_analyst','session-ticket_analyst'],
    ]);
  });
});

function reasoningBlocks(root) { return root.findAll(node=>node.type==='details' && node.props.className==='reasoning-block'); }

test('reasoning streams open, collapses on prose, manually expands, persists and isolates delegation', async()=>{
  let channel;
  const response=()=>new Response(new ReadableStream({start(c){channel=c;}}));
  const emit=async(type,data)=>{await act(async()=>{channel.enqueue(new TextEncoder().encode(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`));});};
  await mounted(async(root,requests,values)=>{
    await send(root,'请思考并委派');
    await emit('session',{session_id:'reasoning-session'});
    assert.equal(reasoningBlocks(root).length,0);
    await emit('reasoning',{delta:'总控思考'});
    assert.equal(reasoningBlocks(root)[0].props.open,true);
    await emit('reasoning',{delta:'追加'});
    assert.match(text(reasoningBlocks(root)[0]),/总控思考追加/);
    await emit('agent_reasoning',{delta:'专家思考',agent_name:'ticket_analyst',parent_call_id:'c1'});
    assert.equal(reasoningBlocks(root).length,2);
    assert.match(text(reasoningBlocks(root)[1]),/思考过程 \(ticket_analyst\)/);
    await emit('agent_token',{delta:'专家正文',agent_name:'ticket_analyst',parent_call_id:'c1'});
    assert.equal(reasoningBlocks(root)[1].props.open,false);
    assert.equal(reasoningBlocks(root)[0].props.open,true);
    await emit('token',{delta:'最终答案'});
    assert.equal(reasoningBlocks(root)[0].props.open,false);
    await act(async()=>reasoningBlocks(root)[0].props.onToggle({currentTarget:{open:true}}));
    assert.equal(reasoningBlocks(root)[0].props.open,true);
    await emit('token',{delta:'继续正文'});
    assert.equal(reasoningBlocks(root)[0].props.open,true,'manual expansion survives subsequent tokens');
    await emit('done',{session_id:'reasoning-session',trace:null});
    await act(async()=>channel.close());
    const saved=JSON.parse(values.get('aro.history'))[0].messages.find(m=>m.role==='assistant');
    assert.equal(saved.text,'最终答案继续正文');
    assert.equal(saved.reasoning_blocks[0].text,'总控思考追加');
    assert.equal(saved.reasoning_blocks[1].agent,'ticket_analyst');
    assert.ok(saved.reasoning_blocks.every(b=>b.complete && b.duration_ms>=0));
    assert.match(text(reasoningBlocks(root)[0]),/秒/);
    await send(root,'无思考的第二轮');
    await emit('token',{delta:'普通回答'});
    await emit('done',{session_id:'reasoning-session',trace:null});
    await act(async()=>channel.close());
    assert.equal(reasoningBlocks(root).length,2,'no new block for plain response');
    const latest=JSON.parse(values.get('aro.history'))[0].messages.at(-1);
    assert.equal(latest.text,'普通回答');
    assert.equal(latest.reasoning_blocks,undefined);
  },response);
});

test('no reasoning or empty reasoning never renders a placeholder block', async()=>{
  await mounted(async(root)=>{
    await send(root,'普通问题');
    assert.equal(reasoningBlocks(root).length,0);
  },()=>new Response('event: reasoning\ndata: {"delta":""}\n\nevent: token\ndata: {"delta":"普通回答"}\n\nevent: done\ndata: {"session_id":"s","trace":null}\n\n'));
});
