# @aro/react

React 18/19 + TypeScript 的无样式集成组件，依赖 @aro/client，React 为 peer dependency。版本 1.0.0。**尚未发布 npm，仅完成本地 tarball 验证**。

## 安装与运行示例

仓库根目录：

```sh
npm ci
npm run build:packages
npm run example -w @aro/react
```

在示例页填业务代理地址；默认 /api。示例 Vite 将 /api 代理到本机 8000 后端，便于本地运行（先启动 ARO 后端）；这只是本地开发代理，不是业务登录鉴权代理。生产需使用业务后端或正确配置 CORS。可运行入口 examples/basic.tsx + index.html，`npm run check:example -w @aro/react` 类型检查并构建。已有工作台开发服务提供 /api 代理，更完整集成见 frontend/。

真正发布前，先 pack 两个包，将它们同时安装到独立项目，避免 @aro/client 被当作已发布远端依赖：

```sh
npm pack -w @aro/client --pack-destination tmp
npm pack -w @aro/react --pack-destination tmp
# 业务项目中（React 由宿主提供）
npm install /absolute/path/aro-client-1.0.0.tgz /absolute/path/aro-react-1.0.0.tgz react react-dom
```

## useAroChat

```tsx
const client = useMemo(() => new AroClient({ baseUrl: '/api' }), []);
const chat = useAroChat({ client, sessionId: null, targetAgent: null });
// chat.messages / thinking / toolCalls / trace / approval / isLoading / isApproving / error / sessionId
await chat.sendMessage('查询工单');
chat.stop();
```

sendMessage/approve 的拒绝会更新 error，同时抛出给宿主；按钮处理可 `.catch(...)` 并依靠 error 展示，不能忽略错误 UI。首次对话接收 session 后复用该 ID；sessionId prop 是外部切换入口，不必把每次收到的 ID 立刻回灌为 prop（变化会视为切会话）。client 应稳定 memo；client/sessionId/targetAgent 改变会取消在途工作并清空本地会话。reset 只清本地状态，下一轮按初始 sessionId 创建/加载；不是服务端删除接口。stop/unmount 取消请求，不保证撤销业务操作。

每条消息保存独立 reasoning 来源块，正文开始或本轮结束折叠；thinking 为当前回复的便利视图，不混入正文。工具参数/结果来自事件，专家正文不混入总控。并发发送受互斥保护，切会话后旧事件被 epoch 忽略。审批调用真实 client.submitApproval，绑定 session/approval ID，不自动重试。

## 五个组件

| 组件 | 主要 Props | 语义 |
|---|---|---|
| ChatMessage | role, content, label?, thinking?, toolCalls?, busy?, beforeContent?, renderContent? | 纯文本默认安全渲染；renderContent 插槽交给宿主安全 Markdown，beforeContent 支持多来源思考块 |
| ThinkingBlock | content, collapsed?, active?, agent?, durationMs? | 空文本不渲染；active 流式展开，结束折叠，可手动展开；耗时为浏览器观察值，不是模型计算耗时 |
| ToolCallCard | step: ToolStep, index?, busy? | 真实参数、结果、失败、授权/执行耗时、Hook 异常；可嵌于 li，未收到结果不假定执行成功 |
| TracePanel | trace: AgentTrace\|null, steps?, busy? | steps 可显示在途数据，否则使用 trace.tool_calls；无数据诚实空态 |
| ApprovalCard | approval: Approval, sending, onDecision | 到期/提交中禁用，真实倒计时；宿主将 onDecision 接到 chat.approve 或既有审批函数 |

```tsx
<ChatMessage role="assistant" content={message.content}
  beforeContent={message.reasoning.map(block => <ThinkingBlock key={block.id}
    content={block.content} agent={block.agent} durationMs={block.durationMs}
    active={!block.complete && chat.isLoading} />)} />
{chat.approval && <ApprovalCard approval={chat.approval} sending={chat.isApproving}
  onDecision={approved => void chat.approve(approved).catch(() => undefined)} />}
{chat.error && <p role="alert">{chat.error.message}</p>}
<TracePanel trace={chat.trace} steps={chat.toolCalls} busy={chat.isLoading} />
```

默认不注入 CSS、不带全局主题，可选 `import '@aro/react/styles.css'`。默认 CSS 只提供基础排版，布局和品牌由宿主决定；工作台复用原有 class 名和主题。renderContent 不能使用不受信任的 HTML 注入；工作台的安全 Markdown 禁用 HTML、链接和图片外链保持不变。

库不管理业务专家目录、报告下载、工单面板、本地历史、主题或业务认证；这些保留在宿主层。工作台继续使用自己的 generation/epoch，而非强制迁移到 useAroChat。

验证：`npm test -w @aro/react`，示例类型检查和构建见上；仅源码/ESM 声明/CSS/示例打包，不捆绑 React 运行时。
