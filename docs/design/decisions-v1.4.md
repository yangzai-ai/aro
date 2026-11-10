# v1.4+ 设计决策记录

日期：2026-11-06。状态：**方向已定，尚未实现**。每项启动前仍按工程约定 9.7 出任务简报，细节以简报为准。

本文记录"为什么这么定、放弃了什么"，供后续迭代和贡献者查阅。路线排期见 [ROADMAP](../../ROADMAP.md)。

## 决策总览

| # | 主题 | 决定 | 放弃的方案 |
| --- | --- | --- | --- |
| D1 | 会话模式 | 保持托管模式：ARO 持有完整运行状态 | 彻底无状态（业务后端带快照） |
| D2 | 扩展模型完整化 | Hook 覆盖完整生命周期（会话恢复 → 轮级钩子 → 关闭），Capability 作标准能力打包器，Trace/持久化/HITL 无特例 | 拆成 Hook + LifecycleHook 两套；宿主级基础设施单独一套机制 |
| D3 | 审批跨重启 | 暂保留 Future 挂起，重启即本轮中断 | 现在改用 LangGraph interrupt |
| D4 | 工具执行管线 | 最小加固：钩子不变，收紧语义 | 照搬五段式管线 |
| D5 | MCP | MCP client 能力插件 + ARO MCP 网关 | 每个外部系统手写工具 |
| D6 | 插件生态 | 可信的能力市场，内核不可替换 | 照搬 dsh 的"一切皆可替换" |
| D7 | 前端复用 | client / react / widget 三层 npm 包 | 只提供 iframe 嵌入 |
| D8 | 高并发 | 多实例（A）与流式批量（B）分开设计，后续迭代 | 现在就引入队列与分布式 |

## D1 会话模式：托管

两个概念先分清：

- **展示历史**：用户可见的问和答、会话列表、标题、归档。业务系统存不存、怎么存，ARO 不关心，也不提供。
- **运行状态**：模型下一轮需要的完整上下文。包括问答、工具调用、工具原始返回、思考内容（DeepSeek 思考模式加 tools 时必须回传 `reasoning_content`，否则 400）。只能由 ARO 持有。

决定由 ARO 按 session_id 托管运行状态，业务后端每轮只传新消息和 session_id。

放弃"彻底无状态"的原因：
- token 消耗不会因此减少。模型每轮都要收到完整上下文，和上下文存在哪里无关。控制 token 要靠上下文压缩，这是另一项能力。
- 快照交给业务后端，就得解决防篡改（签名）、敏感数据（加密）、格式版本兼容等问题。
- 审批挂起期间执行停在进程里，本来就做不到无状态。

## D2 扩展模型完整化：回归两个扩展点

### 重要澄清

在讨论"是否把持久化做成插件"时发现设计逐渐复杂化，引入了"宿主级基础设施"概念，并准备为会话存储和 Trace 导出单独建立扩展组（`aro.session_stores`、`aro.trace_sinks`）。这偏离了最初的**一个内核、两个扩展点（BaseTool + Hook）**设计哲学。

用户提出核心问题：Trace、会话存储能不能都基于这两个扩展点实现？审查后发现可以，但需要完整化扩展点 2（Hook → Hook）。

### 决定：Hook 覆盖完整生命周期

**架构**：

```
内核
├── 扩展点 1：BaseTool（模型按需调用）
└── 扩展点 2：Hook（完整生命周期，所有能力用这一套）

Capability = 打包器（Tool + Hook 的任意组合）
```

**Hook 生命周期阶段**（全部可选，默认空操作）：

```python
class Hook(Protocol):
    async def on_start(self, config: dict) -> None:
        """引擎启动时：配置校验、连接建立。"""
    
    async def on_session_load(self, session_id: str) -> AgentState | None:
        """轮开始前：会话恢复。第一个返回非 None 的 state 即用，其余跳过。"""
    
    async def before(self, state: AgentState) -> AgentState:
        """每轮开始（现有 Hook.before）"""
    
    async def before_tool(self, tool_name, tool_args, state) -> tuple[bool, str | None]:
        """工具执行前（现有 Hook.before_tool）"""
    
    async def after_tool(self, tool_name, tool_args, tool_result, state) -> AgentState:
        """工具执行后（现有 Hook.after_tool）"""
    
    async def after(self, state: AgentState) -> AgentState:
        """每轮结束：写盘、Trace 导出（现有 Hook.after）"""
    
    async def on_session_close(self, session_id: str) -> None:
        """会话关闭：删除存储。"""
    
    async def on_stop(self) -> None:
        """引擎停止时：连接释放。"""
```

现有 Hook 完全兼容，就是只用了中间四个阶段（before/before_tool/after_tool/after）的 Hook。

**各能力的实现**：

| 能力 | 使用的阶段 | 特殊性 |
|---|---|---|
| Trace | before_tool, after_tool, after（导出） | 无 |
| HITL | before_tool（审批拦截） | 无 |
| Memory | before（读上下文）, after（写入） | 无 |
| 会话存储 | on_session_load（恢复）, after（写盘）, on_session_close（删除） | on_session_load 返回 AgentState\|None，内核取第一个非 None |
| 配置校验 | on_start | 无 |
| 多实例心跳 | on_start（注册）, on_stop（注销） | 无 |

所有能力都是标准 Capability，打包 Tool 和 Hook 的任意组合，没有任何特例。

### 放弃的方案

1. **拆成 Hook + LifecycleHook 两套**：会引入概念分裂，用户不清楚什么时候该用哪个。
2. **宿主级基础设施单独一套机制**：为会话存储、Trace 导出单独建 `aro.session_stores`、`aro.trace_sinks` 扩展组。这会让架构变成"标准能力用 Capability，基础设施用另一套"，失去一致性。

### 技术细节：on_session_load 签名不同

`on_session_load` 返回 `AgentState | None`，和其他钩子（接收并返回 state）签名不同。这是本质区别：它是**创建**状态，不是**修改**状态。内核调用逻辑：

```python
state = None
for hook in hooks:
    state = await hook.on_session_load(session_id)
    if state is not None:
        break  # 第一个返回非 None 的胜出
```

这个特殊性在文档里明确说明，但不影响整体架构的一致性。

### 对后续迭代的影响

- Trace 可以从内核移出，改成标准 Capability（v1.6+，非紧急）
- 会话持久化直接用 Hook 实现，不需要单独的 `session_stores` 扩展组
- 插件生态只需一个 entry point 组：`aro.capabilities`，不需要多套机制
- v1.5 简报需要重写：不再是"通用装配 + 持久化"，而是"Hook 完整化 + 会话持久化 Capability"

## D3 审批跨重启：暂不改 interrupt

现状：审批通过 Future 挂起在进程内，`/approve` 回调来唤醒它。

决定：保留现方案。服务重启时等待中的审批中断，**未批准的写操作不会执行**，用户需要重新发起。这是安全方向的失败：不会出现误写数据，只影响体验。

暂不改用 interrupt 的原因：
- interrupt 恢复时会重跑整个节点，同一批里已执行的写操作会重复执行。要避免，必须把工具改成逐个执行，并且做到幂等。
- Hook放在 ContextVar 里的身份、委派预算，恢复后会丢失，需要重新设计传递方式。
- 恢复走新的 HTTP 请求，SSE 流也要重新接上。

**触发条件**：出现以下任一需求时再做：等几小时甚至几天的长时审批；多实例部署下审批落到其他实例，且粘性路由或转发无法满足。D2 的逐工具落盘可以直接作为它的基础。

## D4 工具执行管线：最小加固

核实到的现状：四个Hook（Trace、HITL、Memory、Delegation）都不改参数、不改结果，**没有现存漏洞**。但契约里没有任何禁止，存在以下缺口：

- 所有 before_tool 共用同一个可变 `tool_args`，执行时用的是这个活对象。审批只给人看副本，执行前不比对。排在 HITL 后面的Hook可以在批准后改参数。
- Hook顺序由 yml 书写顺序决定，只有 Trace 被强制排在首位。安全性依赖配置作者怎么写。
- after_tool 抛异常没有兜底：工具已经执行，后续Hook、tool_result 事件和同批剩余调用全部跳过。
- ToolResult 可变：Trace、ToolMessage、前端事件三处看到的结果可能不一致。
- 内核没有统一超时，各工具自行处理。

决定：两个钩子和函数签名不变，由内核强制执行以下语义：
1. before_tool 只能放行或拒绝。内核冻结一份参数，给每个Hook传副本，执行时用内核自己那份。这样Hook顺序不再影响安全。
2. after_tool 只读：参数和结果都传副本或只读对象；每个Hook的异常单独隔离，记进 Trace，后续流程照常进行。
3. BaseTool 增加可选超时，由内核计时，只计执行时间，不含审批等待。**不做通用重试**，因为写操作大多不能安全重放。
4. Trace 把"等待授权"和"执行"分开计时。
5. 写进 CONVENTIONS 作为硬约束，并配"恶意Hook"测试：Hook篡改参数和结果，断言实际执行的仍是原参数，审计记录的仍是真实结果。

不照搬五段式的原因：目前没有改参数、改结果的真实需求；契约越小，插件生态越稳。以后确有需要（比如结果脱敏），再新增显式的改写钩子，这是纯增量改动。**必须在插件生态开放之前完成**：先收紧契约不会破坏兼容，等第三方插件依赖了"可以改参数"再收紧，就会破坏兼容。

## D5 MCP

- **对接现成 MCP Server**：做成 MCP client 能力插件。在配置里列出要连接的 Server，自动发现它们的工具；按 MCP 工具标注（只读或破坏性）映射为 read 或 write，再叠加白名单。工具必须走 D4 的统一管线，也就是权限、写审批、Trace 都要经过，否则每接一个 MCP Server 就多一个绕过审批的口子。现有 `MCPTool`（`tool_adapter.py`）是基础，需要先核对它的权限映射和管线接入情况。
- **ARO MCP 网关**：帮业务系统把已有接口变成 MCP Server。
  - 输入是 OpenAPI 文档，加一份配置：开放哪些接口（白名单，默认不全开）、每个接口的读写属性、鉴权方式。
  - 凭据只放在网关里，永远不进入模型的工具参数。这也补上了 HTTP 适配器缺少企业鉴权的问题。
  - 不方便出 OpenAPI 的系统，提供 Python SDK，用装饰器把函数暴露成工具。
  - 形成的链路是：业务系统 → 网关 → ARO 的 MCP client。其他 MCP 客户端也能复用。
- **OpenAPI 直转工具**保留，作为简单场景的轻量路线，不用额外部署网关。
- **ARO 专家作为 MCP Server 对外提供**：排在后面。

## D6 插件生态：可信的能力市场

参考 deepseek-harness（下称 dsh）。值得学的有：分发单元是"包 + 配置补丁"，profile 有序叠层，包元数据声明兼容的框架版本，用静态 JSON 文件做插件索引，配置错误在加载期就暴露。

和 dsh 的刻意区别：
- **内核不可替换**。dsh 连 agent 循环都可以换；ARO 的循环、审批拦截、写操作强制走 HITL 是企业安全底线，插件只能通过 BaseTool 和 Hook 接入。
- **先可信，再丰富**。dsh 插件运行在宿主进程里，没有权限声明，也没有签名。ARO 的插件必须回答三个问题：它能做什么、碰哪些系统、谁批准安装的。

形态：
- PyPI 包，命名 `aro-plugin-*`。pyproject 里通过 entry point 声明能力入口、兼容范围（如 `aro>=1.4,<2`）、权限清单（提供哪些工具、读还是写、访问哪些外部域名）、Pydantic 配置模型。
- profile 就是项目的 `config/*.yml`，支持分层叠加：平台基线 → 项目 → 环境，并提供命令打印最终合成的配置。
- `aro plugin add` 自己实现，不透传给 pip。它负责锁定版本、校验兼容范围、展示权限清单并等待确认、写入 profile。插件带来的工具**默认不进白名单**。
- 来源：默认企业私有 PyPI，加白名单和 hash 锁定，签名后续再做。插件市场是企业自托管的静态 JSON 索引。
- 分级：官方、认证、社区三级。起步只做官方插件：先把现有 capabilities 和 OpenAPI、MCP 桥接改造成插件形式，用它们验证插件契约。

不学的部分：运行中热插拔（和编译后的静态图冲突）、上百个细粒度扩展点、前端插件槽位（以后有面板需求再议）。

## D7 前端复用：三层组件库

- `@aro/client`：协议层，负责 SSE 解析、事件类型、审批、重连，零 UI 依赖。
- `@aro/react`：组件层，包含对话、思考块、审批卡片、Trace 面板，以及 `useAroChat`。
- `@aro/widget`：WebComponent 形式的 `<aro-chat>` 悬浮窗，给不用 React 的系统嵌入。

现有工作台改造成基于这三层搭建的示例应用。典型部署是"业务前端 → 业务后端 → ARO"，所以 `@aro/client` 要支持指向业务后端代理，token 不进浏览器。前提是 **SSE 事件协议有版本化的正式文档**，业务后端可以照着透传。配套提供 Trace 查询 API。

## D8 高并发：分场景，后续迭代

| 场景 | 判定 | 方案要点 |
| --- | --- | --- |
| A 对话并发 / 高可用 | 单实例承载不了在线会话，或要求高可用 | 基于 D2 的共享会话存储；会话路由；审批跨实例转发或粘性路由 |
| B 流式批量分析 | 有持续推送的数据需要 LLM 处理 | 任务队列 + Worker 池 + 背压 + 结果回调，不走 SSE |

场景 B 的瓶颈在 LLM 的吞吐和成本，不在框架本身。设计重点是"漏斗"：先用规则或小模型过滤、聚合，只把少量数据交给 Agent。结果回调（Webhook）和场景 B 一起做。

## 推进顺序

```
D4 管线加固（✅ 完成）
→ D2a 通用装配（entry point、服务协议、api/ 边界）
→ D2b 会话持久化（建在通用装配之上）
→ 对接（事件协议文档、Trace 查询 API、D7 组件库）
→ D6 插件生态（含 D5，完整权限清单、plugin add CLI）
→ D8 规模化
```

D4 完成后，D2a 是下一步，原因：去除 `api/` 对具体能力的直接依赖是持久化和后续插件生态的前提；改动范围小，只影响 api/ 层和 pyproject.toml，内核不动。
