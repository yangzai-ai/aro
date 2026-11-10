# 插件发现、Hook 生命周期与宿主服务

v1.5 仍只有 BaseTool 与 Hook 两个扩展点，Capability 仅作打包器。没有独立的会话存储插件组或 Trace 导出插件组，本版本没有实现持久化插件。

## 安装、发现与启用是三件事

外部包可以在 pyproject.toml 声明：

```toml
[project.entry-points."aro.capabilities"]
my_plugin = "my_package:MyCapability"
```

类入口必须可无参构造，框架随后调用既有 `initialize(config)`；构造器内不要建立需要异步释放的连接，资源放到 Capability.start 或 Hook.on_start。也可以指向 `get_capability(config)` 形式的工厂，由工厂自行负责构造过程未返回对象时的失败清理。返回值必须是 Capability。

安装这个包只注册元数据，不自动启用它。YAML 仍显式声明启用能力：

```yaml
capabilities: [my_plugin]
capability_config:
  my_plugin:
    option: value
```

也支持等价映射写法：

```yaml
capabilities:
  my_plugin:
    option: value
```

同一能力不能同时在映射与 capability_config 中定义配置；冲突明确报错。YAML 顺序决定能力装配顺序。

优先级：显式传入 `AgentEngine.create(..., capabilities=[...])`（包括 []）> 配置里的显式 module > 同名 entry point > 既有 capabilities.<name>.capability 模块约定。Entry point 已选中却损坏时不会静默回退到别的实现。

`core.kernel.discovery.discover_capabilities(names=None, configurations=None, *, strict=False, owned=None)` 是独立发现接口。显式无参调用会加载所有条目；Engine 总是传配置选中的名称过滤，未启用条目不会调用 load。独立宽容发现会记录失败条目名与异常类型并继续，strict=True 则失败关闭。需要集中回滚时传入 owned 列表，类实例在 initialize 之前登记所有权；异步 Engine.create 使用此机制清理已加载/部分初始化对象。

推荐资源型部署始终使用 `await AgentEngine.create(...)` 与 `await engine.close()`：异步创建负责发现/能力启动/Hook 启动失败时的回滚。同步构造保留原使用方式及“需要异步 Capability.start 则明确报错”的约束，不提供在同步构造器里 await 清理的保证。

## 完整生命周期

Hook 仍是 ABC；四个既有阶段的签名、逻辑和顺序保持原样。新方法都有默认无操作实现（on_session_load 默认 None），调用处兼容 hasattr 检查。

```text
Capability.start（异步 create）
  → 装配 tools/hooks、绑定工具
  → Hook.on_start(config)（正序，每 Engine 一次）
  → 每个用户轮：on_session_load(thread_id)，首个非 None 胜出
  → 追加本轮 HumanMessage → 原图循环与四个既有钩子
  → 会话关闭：on_session_close(thread_id)（逆序，每会话一次）
  → 引擎关闭：on_stop()（逆序，仅已进入启动的 Hook）
  → Capability.close → 模型客户端/检查点资源释放
```

`AgentEngine.create` 返回前等待 Hook 启动；同步 `AgentEngine(...)` 不启动异步 Hook，首次 run 前自动等待，也可显式 `await engine.start()`。并发 start 共用一个启动任务；一个 Engine 仍只允许一个用户轮在途。启动失败或取消会关闭已进入启动的 Hook，包括部分启动失败的那个，然后关闭能力等资源。

on_start 接收独立的 Agent 配置副本（能力、工具、系统提示词与 YAML 的模型覆盖项），不是 LLMConfig，不自动传环境变量中的模型密钥；部署配置仍是受信任输入。

会话加载在每次用户 run 的 before 钩子之前、追加用户输入之前执行一次，不在工具循环内反复加载。session_id 来自 state.context.thread_id；缺失时生成。恢复消息必须是 BaseMessage 列表，context 必须是字典，thread_id/tenant_id/user_id 与调用方已提供身份冲突时拒绝；调用方未提供身份时也不能从恢复记录注入其他身份。顶层 tenant_id 维持独立语义并校验冲突。返回状态深拷贝，调用方 context 合并保留，本轮输入只追加一次；无恢复结果时保持原状态和消息对象身份。

API 的关闭链先取消在途用户轮，再通知会话关闭，最后关闭 Engine 或专家 scope；DELETE、过期与服务退出共用幂等清理。Engine.close 也通知尚未关闭的 CLI/嵌套会话。清理中的单个 Hook 异常/自身取消不跳过其余资源；调用者取消不会遗弃清理任务。普通错误只记录类型，异常原文不写入日志。显式 Engine/会话关闭失败会在尝试其余清理后向调用者报告；宿主的批量过期/退出清理逐项记录失败并继续清理其他会话。

本版本不开放历史会话查询、跨进程恢复、存储后端配置或审批续跑，不从 checkpointer 驱动恢复，也不自动重放工具。

## Hook 暴露宿主服务

`Hook.provides` 默认为空映射，打包器可在创建 Hook 实例后声明：

```python
hook.provides = {"approvals": approval_service}
```

`core/kernel/services.py` 只定义 ApprovalService、ExpertDirectory、ExpertInfo、LocalExpert 类型协议以及 hook_service 查询函数。审批服务需要 mode 和 decide；同名宿主服务多个提供者会明确报错。HITL 打包器只增加 provides 元数据，HITLHook/Future 审批逻辑不变；API 不依赖 HITL 类型，可接入其他符合协议的实现。

专家目录需要在尚无 Engine 的首屏加载，所以由项目将目录工厂显式注入宿主 `create_app(..., expert_directory=...)`。Helpdesk 在 projects/helpdesk/services.py 适配 MultiAgentCapability 和 LocalAgentHandle，检查本地直连要求；关闭专家 scope 仍由原所有者负责。目录读取只加载启用 multiagent 的元数据，不启动专家或加载知识模型。通用 API 未注入目录时为空；Helpdesk 项目入口保持原专家功能。

API 对任意审批实现检查 mode=api，不猜具体能力名；专家直连仍禁止非 read 工具，主会话与专家会话的绑定、互斥、容量、Trace 和清理规则保留。Trace 已是内核服务，继续复用 TraceHook，不新增 TraceService。

所有具体能力导入留在能力/项目层。core/api 不导入 capabilities 的具体类，新增 AST 测试持续约束此边界。
