# @aro/client

独立 TypeScript/ESM 协议层，无 UI 依赖，浏览器或 Node.js 18+（原生 fetch/ReadableStream）。包版本 1.0.0，对应 SSE v1；不是框架版本号。**本次仅本地 pack 验证，尚未发布 npm**。

## 安装与构建

仓库根目录 `npm ci`、`npm run build:packages`。工作台通过 npm workspaces 使用本地包。独立业务项目在真正发布前使用 tarball：

```sh
# 仓库根目录，先构建
npm pack -w @aro/client --pack-destination tmp
# 在业务前端目录
npm install /absolute/path/to/aro/tmp/aro-client-1.0.0.tgz
```

不需内核依赖，不读取 process.env；baseUrl 应指向业务代理（例如 /api），部署密钥应由业务后端保存。

## 对话与会话

```ts
import { AroClient } from '@aro/client';
const client = new AroClient({ baseUrl: '/api', queryRetries: 2, retryDelayMs: 250 });
let sessionId: string | null = null;
const abort = new AbortController();
await client.chat('查询待处理工单', {
  sessionId, targetAgent: null, signal: abort.signal,
  onEvent(event) {
    if (event.type === 'session') sessionId = event.data.session_id;
    if (event.type === 'reasoning') console.log('独立思考展示', event.data.delta);
    if (event.type === 'error') console.log(event.data.message, event.data.terminal);
    if (event.type === 'approval_required') console.log('展示审批', event.data);
  },
  onToken(delta) { console.log(delta); },
});
// 下一次调用传 sessionId；专家直连传 targetAgent，并保持会话目标一致。
```

也支持 `chat(sessionId, message, options)`。首条 chat(sessionId=null) 创建会话；不存在 createSession()。身份由服务端决定，客户端不传 tenant_id/user_id。不要每次请求都传 null 而期待保留上下文。

onEvent 支持全部 13 种已存在的事件，另有 onToken/onToolCall/onToolResult 便利回调。reasoning/agent_reasoning 不拼入正文；agent_token 不拼入总控正文。未知事件忽略。onError 用于传输/解析/回调错误，服务端 error 通过 onEvent 交付。

聊天只发一次 POST。断流无终止事件抛 UnknownOutcomeError；无法确定写入是否执行，不自动重发。主动 AbortSignal 取消继续抛取消错误，已执行业务操作不会撤销。done 与 terminal=true error 标记本轮终止，非终止 error 后继续读取。HTTPError 保留 status，404 可提示会话已过期。

## 审批与 Trace

```ts
await client.submitApproval(sessionId!, approvalId, true); // 不自动重试
const trace = await client.getTrace(traceId, { sessionId: sessionId!, signal: abort.signal });
const retained = await client.listTraces(sessionId!, { limit: 50, offset: 0 });
```

getTrace 必须传 sessionId，SDK 放入 X-ARO-Session。listTraces 的 ID 在路径中；均受现有 Bearer 保护（如启用）。GET 对网络 TypeError、429/502/503/504 指数退避，默认最多 2 次重试（配置 0..5），基准延迟默认 250ms（0..10000）。取消立即停止等待；401/403/404 不重试。聊天和审批永不重试。token 为可选访问凭据，仅在可信部署允许时使用；不把服务端共享部署 token 嵌入公开前端。

Trace 只查询该活跃会话当前保留的已结束记录，HTTP 默认最近一轮，非历史审计。未新增用户级授权；代理必须校验业务登录态对应的 session_id，不能只转发浏览器任意 ID。

## 类型与验证

导出 AroEvent/EventMap、ToolCallEvent/ToolResultEvent、ToolStep、AgentTrace、Approval、SSEParser、HTTPError、UnknownOutcomeError。源码使用严格 TypeScript，dist 含 ESM 和 .d.ts；不使用运行时 React。SSEParser 是增量解析器，收到完整空行才交付事件，忽略未知事件/id/retry。

`npm run build -w @aro/client`、`npm test -w @aro/client`。协议完整字段和代理边界见仓库 docs/protocols/sse-events-v1.md。npm tarball 不自动包含仓库文档；集成方应同时固定所依赖的协议版本。
