import {useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AroClient} from '@aro/client';
import {useAroChat, ChatMessage, ThinkingBlock, TracePanel, ApprovalCard} from '@aro/react';
import '@aro/react/styles.css';
function Example() {
  // Default points to the business proxy, not a browser-held deployment key.
  const [baseUrl,setBaseUrl]=useState('/api');
  const client=useMemo(()=>new AroClient({baseUrl}),[baseUrl]);
  const chat=useAroChat({client});
  const [text,setText]=useState('查询待处理工单');
  return <main><h1>ARO 业务前端集成示例</h1>
    <label>代理路径 <input value={baseUrl} onChange={e=>setBaseUrl(e.target.value)} disabled={chat.isLoading}/></label>
    <p>单次聊天 POST；断流不重放。请在代理校验登录态和会话归属。</p>
    {chat.messages.map(m=><ChatMessage key={m.id} role={m.role} content={m.content} beforeContent={m.reasoning.map(b=><ThinkingBlock key={b.id} content={b.content} agent={b.agent} durationMs={b.durationMs} active={!b.complete && chat.isLoading}/>)} />)}
    {chat.approval && <ApprovalCard approval={chat.approval} sending={chat.isApproving} onDecision={value=>void chat.approve(value).catch(()=>undefined)} />}
    <form onSubmit={e=>{e.preventDefault();void chat.sendMessage(text).catch(()=>undefined);}}><label>问题 <textarea value={text} onChange={e=>setText(e.target.value)} /></label><button disabled={chat.isLoading || !text.trim()}>发送</button><button type="button" onClick={chat.stop} disabled={!chat.isLoading}>停止</button><button type="button" onClick={chat.reset}>新对话</button></form>
    {chat.error && <p role="alert">{chat.error.message}</p>}
    <TracePanel trace={chat.trace} steps={chat.toolCalls} busy={chat.isLoading} />
  </main>;
}
createRoot(document.getElementById('root')!).render(<Example/>);
