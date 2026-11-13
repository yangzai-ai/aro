import {AroClient, type AroEvent, type AgentTrace} from '@aro/client';
import {ChatMessage,ThinkingBlock,ToolCallCard,TracePanel,ApprovalCard,useAroChat} from '@aro/react';
const client = new AroClient({baseUrl:'/api'});
const event = (event:AroEvent) => { if(event.type === 'agent_done') console.log(event.data.agent_name); };
void client.chat('hello',{sessionId:null,onEvent:event});
void client.chat(null,'hello',{targetAgent:'expert'});
void client.getTrace('t',{sessionId:'s'});
void client.listTraces('s',{limit:1});
const trace:AgentTrace={trace_id:'t',thread_id:null,user_input:'',agent_response:'',tool_calls:[],duration_ms:null,error:null};
export function Host(){
 const chat=useAroChat({client});
 return <><ChatMessage role="assistant" content="reply" renderContent={text=><strong>{text}</strong>} />
 <ThinkingBlock content="thinking" active={chat.isLoading}/>
 <ToolCallCard step={{call_id:'c',tool_name:'query',args:{}}} />
 <TracePanel trace={trace}/>
 <ApprovalCard approval={{approval_id:'a',tool_name:'write',args:{},expires_at:1}} sending={chat.isApproving} onDecision={approved=>void chat.approve(approved)} /></>;
}
