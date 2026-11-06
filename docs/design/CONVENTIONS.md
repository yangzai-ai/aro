# 工程约定（CONVENTIONS）

> 本文件是整个框架的"工程宪法"。任何人（包括 AI）写代码前必须读，写代码时必须遵守。
> 目的：约束实现、防止代码腐烂（屎山）。当前目录与运行方式见仓库 README；版本里程碑见[路线图](../../ROADMAP.md)。

---

## 0. 最高原则

0. **内核极简，能力皆插件（第一原则）**：内核只有「LLM 循环 + 扩展点机制」，对任何具体能力零认知。能力（工具/知识库/记忆/HITL/多Agent/文档生成）通过扩展点挂载。**加能力绝不改内核**；内核里**禁止出现 `if xxx_enabled` 这类对具体能力的判断**。
1. **契约优先**：跨模块的接口（抽象基类 / Protocol）先定义、先稳定，再写实现。改接口是大事，要显式讨论。
2. **垂直切片**：每次交付一个能端到端跑通的切片，不水平摊大饼。见 [ROADMAP.md](../../ROADMAP.md)。
3. **可运行永远优先于完整**：宁可功能少但能跑、能测，不要功能全但串不起来。
4. **按需引入复杂度**：P0 立骨架，P1 按需叠加，P2 真实需求驱动。不提前抽象、不过度设计。
5. **删代码比加代码更值得表扬**：重复、冗余、"可能以后用得上"的代码一律不留。

### 三层模型（第一原则的落地）

| 层级 | 内容 | 可选性 | 位置 |
|------|------|--------|------|
| **内核 Kernel** | LLM 循环、扩展点机制、Trace | 不可选 | `core/kernel/` |
| **标准能力 Capability** | 工具、知识库、记忆、HITL、多Agent、文档生成 | 可插拔，配置启用 | `capabilities/` |
| **横切治理** | 安全、审计、成本、评测 | P2，默认关 | 未来 |

两个扩展点：**工具**（能力即工具）+ **中间件链**（执行前后钩子）。可选性只针对能力层——内核、LLM Provider、状态、路由**不做成插件**（避免过度可配置）。

---

## 1. 技术栈与版本

- **语言**：Python 3.11+（统一，不混用更低版本）
- **包管理**：`uv`（快、锁定可复现）。依赖写在 `pyproject.toml`，不手动维护 `requirements.txt`
- **Agent 引擎**：LangGraph
- **类型检查**：`mypy`（strict 模式，见第 5 节）
- **代码风格**：`ruff`（format + lint 二合一）
- **测试**：`pytest`
- **异步**：I/O 密集路径（LLM 调用、API、检索）统一用 `async/await`，不混用同步阻塞调用

---

## 2. 目录结构与依赖方向

当前物理目录以仓库 README 与源码为准；下述支撑设施分层为设计规则，非现存包清单。这里规定**依赖方向**（谁能 import 谁），这是防屎山的核心规则。

```
依赖只能从上往下，严禁反向或横向穿透：

  projects/*            （业务项目：最上层，配置装配能力）
      ↓
  capabilities/*        （标准能力：可依赖 core 契约 + 支撑设施，彼此不依赖）
      ↓
  integration/ · knowledge/   （支撑设施：被能力按需依赖）
      ↓
  core/ (kernel + 契约)  （内核：只依赖标准库和第三方库，对能力零认知）
```

**硬性规则**：
- `core/kernel/` **绝不** import `capabilities/`、`projects/`、`integration/`、`knowledge/`。内核是地基，不知道任何能力存在。**内核里禁止出现对具体能力的 `if`。**
- `core/` 其余（llm / tools 契约）同样不依赖上层。
- `capabilities/*` 之间**互不 import**（如 memory 不 import knowledge_tool）。需要协作时由 project 配置组合。
- `knowledge/`、`integration/` 之间**互不 import**。
- `projects/helpdesk/` **绝不** import `projects/finance/`。项目之间完全隔离。
- 跨层调用只能通过 `core/` 定义的**契约接口**（Capability / BaseTool / Middleware / LLMProvider），不依赖具体实现类。

> 判断某段代码放哪：问"它依赖谁"。只是循环+挂载机制 → `core/kernel`；是一种可选能力 → `capabilities/`；对接具体外部系统 → `integration`/`knowledge`；具体业务 → `projects`。

---

## 3. 契约（接口）规范

以下接口是框架"宪法级"的稳定契约，定义在 `core/` 下，用 `typing.Protocol` 或 `abc.ABC`。**改动需显式讨论，不得随手改。**

| 接口 | 位置 | 职责 |
|------|------|------|
| `Capability` | `core/kernel/capability.py` | ★ 能力统一封装：声明贡献的 tools / middlewares |
| `Middleware` | `core/kernel/middleware.py` | ★ 执行前后钩子（记忆/HITL 等能力的扩展点） |
| `BaseTool` + `@tool` | `core/tools/` | ★ 工具定义，含 `permission` 字段（能力即工具的扩展点） |
| `AgentState` | `core/kernel/state.py` | 状态结构（TypedDict） |
| `LLMProvider` | `core/llm/provider.py` | 统一 LLM 调用（OpenAI 兼容），屏蔽 DeepSeek/Ollama 差异 |
| `AgentHandle` | `capabilities/multiagent/handle.py` | 统一 Agent 调用接口，屏蔽 Local/Remote 差异 |
| `AgentRegistry` | `capabilities/multiagent/registry.py` | Agent 统一登记与查找 |
| `Repository` | 由各 project 定义接口，core 不预设 | 数据源抽象（mock/api 可换） |

> 前三个（Capability / Middleware / BaseTool）是插件机制的地基，**v0.1 必须定稳**。

v0.8 经批复增加通用生命周期：Capability 的
`async start()` / `async close()` 默认无操作。异步装配使用
`await AgentEngine.create(...)`，先 start 再收集工具；结束时 close，启动失败或取消也清理。
原能力无需修改；旧同步构造仅用于没有覆盖 start 的能力，误用须明确报错。

**接口设计规则**：
- 接口只定义"做什么"，不含实现细节。参数和返回值用明确的类型（Pydantic model 或 dataclass），不用裸 dict 传递结构化数据。
- 每个接口配一个最简单的可用实现 + 一个 mock/fake 实现（用于测试）。
- 接口变更要同步更新所有实现，不允许"先改一个实现，接口对不上"。

---

## 3.1 运行时信息的三条通道（不要试图三合一）

能力拿"运行时信息"有三条通道，**职责不同、互不替代**。新能力按用途选通道，不要把它们合并
（曾评估过的 debt2「运行时注入收敛」已确认是伪债，见 [debt2 评估](../v1.2-debt2-assessment.md)）。

| 通道 | 承载什么 | 谁写 / 谁读 | 生命周期 |
|------|----------|-------------|----------|
| `bind_tools(tools)` | 工具表（装配期注入） | 内核装配时写；中间件读（如 HITL 判 `write`） | 装配期一次性 |
| `state["context"]`（`RuntimeContext`） | **可序列化**请求数据：`thread_id`/`tenant_id`/`user_id` 等 | 内核/宿主写；中间件读 | 请求级，跨进程/跨轮 |
| `ContextVar` scope | **进程内受信任身份**（从 context 派生校验后的 namespace/委派 scope） | 中间件 `before` 写、`after` 清；**工具只读** | 轮内窗口，用完归零 |

**数据流是单向分层的**：`state["context"]` →（中间件 before 校验+派生）→ `ContextVar` →（工具 run 读）。

**硬约束**（破坏任一条都是 bug，不是优化空间）：
- **身份绝不走工具可见的 state**：工具参数由 LLM 生成，身份一旦放进工具能读的位置就可被伪造。
  身份只走 ContextVar 这条"内核/中间件写、工具只读"的受信任侧信道；工具 schema **有意不含身份字段**。
- **对象身份语义不可序列化**：multiagent 靠 `HumanMessage` 对象身份判断"同一轮跨子轮"，无法塞进 context。
- **轮内临时绑定 ≠ 请求级数据**：ContextVar 的 set/reset 对应轮内窗口，与 context 的请求级语义不同。

---

## 4. 命名规范

- **文件/模块**：`snake_case.py`
- **类**：`PascalCase`。接口类不加 `I` 前缀（用 `LLMProvider` 不用 `ILLMProvider`）
- **函数/变量**：`snake_case`
- **常量**：`UPPER_SNAKE_CASE`
- **工具名**（LLM 可见）：`snake_case`，动词开头，语义清晰（`query_tickets` 不是 `qfd` 或 `TicketQuery`）
- **私有**：单下划线前缀 `_internal_helper`
- **异步函数**：不加 `async_` 前缀，靠签名区分
- **布尔**：`is_` / `has_` / `should_` 开头

---

## 5. 类型与数据结构

- **全量类型注解**：所有函数签名（参数 + 返回值）必须有类型注解。`mypy` strict 通过是合并前提。
- **禁止裸 dict 传结构化数据**：模块间传递的结构化数据用 Pydantic model 或 dataclass。dict 只用于真正动态的键值场景。
- **Pydantic v2** 用于：工具参数校验、配置加载、API 请求/响应模型。
- **配置**：用 `pydantic-settings`，从环境变量 + YAML 加载。**严禁硬编码**密钥、URL、模型名、路径——全部走配置。
- **不可变优先**：能用 frozen dataclass / 不可变结构的地方不用可变全局状态。

---

### 中间件工具钩子硬约束（v1.4）

- `before_tool` 只能放行或拒绝，不得修改 `tool_args` 影响执行。内核在整批调用进入钩子前深拷贝参数，每个钩子另收独立副本；执行也使用独立副本，批准 A 只执行 A。
- `after_tool` 只读观察，不得修改 `tool_args`、`tool_result` 或 `state.messages`。内核逐钩子复制参数、结果、消息视图，丢弃消息改写；消息原对象身份保持不变。
- `after_tool` 可以写 `state.context` 或外部存储，不算违规。此类写入不是事务，钩子失败不会回滚已发生的副作用。
- `after_tool` 的普通异常逐项隔离，Trace 记录中间件类名、钩子名和异常类型（不记录可能含密钥的原始异常文本），继续后续钩子、结果事件和同批调用。取消继续传播，不当作普通异常吞掉。
- 工具可设置 `timeout_seconds`（默认 `None`），内核只对 `run()` 施加协作式异步超时，不含审批等待；超时转失败，不自动重试，不能保证撤销外部写入。

## 6. 配置与密钥

- 所有密钥（API Key 等）走环境变量，通过 `.env`（本地）加载，`.env` **必须** 在 `.gitignore` 里。
- 提供 `.env.example` 列出所有需要的变量名（不含真实值）。
- 配置分两类：**声明式配置**（YAML：提示词、工具列表、模型参数）和**运行时密钥**（环境变量）。
- YAML **只做声明式配置，不表达流程逻辑**（流程一律代码）。这是和 Dify 划清界限的红线。

---

## 7. 错误处理

- **不吞异常**：禁止 `except: pass` 和裸 `except Exception` 后不处理。要么处理，要么向上抛。
- **分层异常**：`core/` 定义框架级异常基类（如 `AgentError`、`ToolError`、`LLMError`），各层继承。
- **错误分类**（对接外部系统时）：
  - 瞬态错误（超时、429、5xx）→ 可重试（弹性模式，P1）
  - 可恢复错误（工具返回不合预期）→ 反馈给 LLM 让它重试/换路
  - 致命错误（认证失败、配置错误）→ 快速失败并清晰报错
- **面向 LLM 的错误**：工具执行失败时，返回给 LLM 的错误信息要可读、可指导下一步，不是栈回溯。
- **日志**：用标准 `logging`，不用 `print`。关键路径（LLM 调用、工具执行、Agent 路由）打结构化日志。

---

## 8. 测试

- **契约接口必测**：每个 `core/` 接口的每个实现都要有单元测试。
- **mock 外部依赖**：测试不依赖真实 LLM API / 向量库 / 网络。用 fake 实现或 mock。
- **每个垂直切片有端到端测试**：验证该切片的验收标准（见 [ROADMAP.md](../../ROADMAP.md)）。
- **测试跟着代码走**：新增功能必须带测试，不允许"以后补"。
- **快**：单元测试套件应秒级跑完（慢测试隔离到单独标记）。

---

## 9. 给 AI / 协作者的实现纪律

> 这一节专门针对"AI 一股脑生成屎山"的风险。每次让 AI 写代码时，这些是硬约束。

**铁律**（按优先级排序，冲突时上面的优先）：

0. **分轻重缓急，严格按优先级（P0 → P1 → P2）**：
   - P0 必须先做；当前完成状态与未来优先级见[路线图](../../ROADMAP.md)
   - P1 核心稳定后做（v0.1-v0.3 跑通后再碰 P1）
   - P2 真实需求驱动（没人用不做，别"可能以后用得上"）
   - **不按优先级就是屎山的开始**
   
1. **一次只做一个切片**：不要跨切片一次性生成大量代码。按 [ROADMAP.md](../../ROADMAP.md) 的里程碑推进。每个切片的验收标准必须全部达成才能进下一个。

2. **先读接口再写实现**：实现任何模块前，先确认它依赖的 `core/` 接口已定义且稳定。接口不存在就先定接口（并说明）。

3. **不偷偷扩展范围**：只实现当前切片要求的，不"顺手"加未规划的功能、参数、抽象。

4. **不造重复**：写之前先搜有没有已存在的工具函数/基类。重复逻辑抽到公共位置。

5. **不留 TODO 占位实现**：要么真实现，要么明确标记为当前切片不做并说明。禁止"看起来实现了其实是空壳"。

6. **保持可运行**：每个切片结束时项目必须能启动、能通过已有测试。不能把 main 分支改到跑不起来。

7. **改接口要喊停**：如果发现某个契约接口设计有问题需要改，先说明原因和影响范围，不要默默改掉导致其他实现对不上。

8. **遵守依赖方向**：写 import 前想一下第 2 节的依赖规则。违反依赖方向的代码一律重构。

---

## 10. 提交与版本

- **小步提交**：一个提交做一件事，提交信息说清"为什么"。
- **main 始终可运行**：不把半成品合进 main。切片在分支上做完、测试通过再合。
- **每个切片一个里程碑**：对应 [ROADMAP.md](../../ROADMAP.md) 的里程碑，可打 tag。

---

## 附：决策速查（和路线图对齐）

- **第一原则**：内核极简，能力皆插件；加能力不改内核，内核禁止对具体能力 if 判断
- 两个扩展点：工具（能力即工具）+ 中间件链；可选性只针对能力层
- LLM：在线 API（DeepSeek）优先，Ollama 本地预留，统一 OpenAI 兼容接口
- 检索模型（Embedding/Reranker）本地化
- MCP：Client 为 P0，Server 自建为 P1（独立进程/同仓库）
- 多 Agent：通过 AgentHandle 直接委派，LangGraph 绑定只留 core/kernel；A2A 双向为 P1
- 记忆：L1/L2 读写 P0，写入治理 P1
- 多租户：接口预留，第一个项目不实现
- 安全/审计/成本/评测：P2，不提前做
- 唯一必须 P0 预留的治理项：工具 `permission` 分级字段
