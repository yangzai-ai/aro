# v0.7 可选多 Agent 委派能力

总控由原有 AgentEngine 运行：LLM 看 Registry 的名称和描述，通过 `delegate_agent` 选择一个
专项 Agent，专项返回结果后总控整合回答。派发使用 LangGraph StateGraph，专项执行仍使用
同一套 Engine/KernelLoop/Tool/Hook，core 未增加多 Agent 分支或接口。

## 何时使用

多 Agent 不是默认选择。单 Agent 配好工具能解决的任务就不应拆分；领域差异大、工具集互相干扰、
需要独立扩展时再拆。helpdesk 默认继续是单 Agent。本版是框架能力演示，不是改造业务项目。
只实现“选择一个专项 → 返回结果 → 总控整合”，没有自由讨论、反复协商、投票或多专项并行。

## 统一边界

```python
class AgentHandle(Protocol):
    name: str
    description: str

    async def invoke(self, task: str, context: AgentContext) -> AgentResult: ...
```

AgentContext 只含可序列化的 thread_id、tenant_id、user_id、parent_call_id、depth；
AgentResult 含 agent_name、answer、success、error、trace。两者均可 JSON round-trip。
接口没有 AgentState、Engine、回调或共享消息列表，总控只依赖 Protocol，不区分实现类型。
测试用一个非 Local 的 JSON 往返 stub 直接经过同一个总控派发图，验证换实现无需改总控。
本版只提供 LocalAgentHandle，未实现 RemoteAgentHandle、A2A、Agent Card 或 Gateway。

LocalAgentHandle 从宿主提供的异步资源作用域取得 Engine，用独立 L1 和新 thread 运行；
只把任务及白名单身份传下去，不把总控历史整包传入。身份来自受信任 state.context，
不在模型的 delegate_agent schema 中，客户端也不能自报。新专项调用不保留上次专项 L1；
后续指代应由总控在 task 中补充必要事实，本版不做专项会话恢复。

## 能力装配与配置

multiagent 是工具 + 绑定Hook的混合 Capability，不需要新的内核扩展点。
工具的 description 包含动态 Registry 清单，agent_name schema 的 enum 对应登记名称；
LLM 根据这些描述做选择，图中无领域关键词 if-else。
Hook绑定可信身份，并在一个完整用户轮次内限制一次委派，工具子轮不会重置预算。
LangGraph 节点仅负责按模型已选择的名称派发，不额外创建第二套路由模型或执行内核。

```yaml
capabilities: [multiagent]
capability_config:
  multiagent:
    timeout_seconds: 180
    agents:
      - module: projects.your_business.agents
        config:
          name: helpdesk_data
          description: 查询工单数量、工单历史与重复的模拟业务数据
          config_path: projects/your_business/config/specialist.yml
tools: [delegate_agent]
```

module 是受信任的本地 Python 装配模块，须暴露 `get_agent(config) -> AgentHandle`。
Registry 拒绝重名、无描述和非法标识；工具对未知对象、无效参数和身份缺失失败关闭。
禁用 multiagent 时不会导入 Handle、Registry、supervisor、项目工厂或专项能力。
其他能力只由项目配置/工厂装配，multiagent 不 import knowledge/memory/hitl。

## Trace 与 SSE

总控最终 Trace 的 delegate_agent 参数标明路由对象，result 内包含 AgentResult 和完整子 Trace：

```text
总控 Trace
└── delegate_agent(agent_name=knowledge_qa, task=…)
    └── result.trace
        └── search_knowledge_base(args, result, duration, success)
```

专项工具的实时 tool_call/tool_result 保留 `agent_name`、`parent_call_id`，
call_id 加调用级 UUID 前缀避免不同模型重复 ID，tool_name 显示为 `专项名/工具名`。
现有前端 TracePanel 因而可以直接展示，无需新增组件。
子 Trace 的原始 call_id 保持专项自身语义，用外层 agent_name 与父调用关联。

另有 agent_start、agent_done、agent_failed、agent_token 事件。
子文本使用 agent_token，不混入总控最终回复的 token，避免页面出现两份回答。
前端当前忽略专属 agent_* 事件，只显示专项工具步骤和总控文本；完整子 Trace 可在最终结果展开。
没有复用同一个 TraceHook；每个父/子 Engine 独立，转发恢复父事件作用域，嵌套事件不会递归。

## 超时、取消和权限边界

每轮最多委派一次，不允许专项再次开启委派。timeout_seconds（默认180、最大600）覆盖等待
资源锁、专项工具/模型执行和资源清理。调用失败/未知对象/超时返回通用失败，异常细节不返回模型；
现有总控最多工具轮数限制继续生效。取消向子执行传递，资源作用域负责 finally 清理。

**v0.7 委派仅支持只读专项**：Local 在运行前拒绝任何非 read 的已启用工具，
所以不能通过 read 的 delegate_agent 绕过 write 的人工确认。子 Agent 的 API 审批归属不在本轮范围，
不宣称子 HITL 已接入 /approve。memory/knowledge/HITL 内部实现均未修改。

失败/超时的最终总控 Trace 记录路由请求与失败；完整成功子 Trace 仅在返回成功结果时嵌入。
取消或异常退出前已送达的专项步骤仍可从实时事件看到；本轮不持久化未完成的子 Trace。

## 成本与资源

相较直接单 Agent，多一次总控路由生成和一次总控整合生成，专项自身也可能多次调用模型。
延迟/费用随调用链增加，不能把多 Agent 当成性能优化。RAG 首次加载 BGE 需要额外时间和显存。

示例项目工厂对同一 config_path 的专项作用域串行化，包括不同 API 会话；
每次调用独立构建 Engine，finally 关闭模型 HTTP 客户端和本地 Qdrant。
知识模型目前每次重新装配，未做共享模型池；会有重复冷启动成本。CUDA/工作线程里的计算不能
被 Python 取消瞬间抢占；取消后线程可能继续完成，磁盘访问清理由适配器锁保护。
外部 CLI/索引器不得同时占用相同 Qdrant 目录。不同配置若指向同一目录也需由宿主管理独占，
示例锁不是分布式锁。不做自动重试或多进程共享。

启动、入库与真实验收见 演示项目说明（历史资料已移出仓库）。
