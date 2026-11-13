# Helpdesk 示例项目

这是 IT 支持业务的完整示例：通过工单查询、更新、人工审批、专家、知识检索和 Word 报告，学习如何组合 ARO。默认业务数据是内存模拟数据，每个独立引擎持有自己的快照，不能用于生产决策。

以下命令在**仓库根目录**执行。环境与模型服务配置见[根 README](../../README.md)，架构边界见[工程约定](../../docs/design/CONVENTIONS.md)。

## 目录：新需求改哪里

| 路径 | 职责 / 什么时候改 |
| --- | --- |
| `config/*.yml` | 提示词、能力组合、工具白名单、数据源，见下表 |
| `repositories/base.py` | Ticket/状态模型及 HelpdeskRepository 契约；对接系统前先读 |
| `repositories/mock.py` | 内存模拟数据，重启/新建引擎会重新生成 |
| `repositories/http.py` | 已实现的 HTTP 适配器，不是空壳；适配上游协议时改这里 |
| `tools/` | query_tickets、update_ticket、get_ticket_history、check_duplicate_tickets 四个工具 |
| `capability.py` | 创建 Repository，将工具打包为 helpdesk_tools |
| `workbench.py` | 共用同一 Repository，收集证据，提供面板与 generate_ticket_report |
| `experts.py` | 本地专家工厂：委派时临时引擎，直连时由 SessionStore 持有作用域 |
| `api.py` | 项目 API：面板、全局专家目录、报告下载、关闭会话 |
| `main.py` | CLI，支持 --config 和 --offline-demo |
| `templates/ticket.docx` | 受控 Word 模板；字段见[模板说明](templates/README.md) |
| `docs/` | 示例知识文档，入库后才能检索 |

通用会话/SSE/审批宿主在根目录 `api/`，可复用能力在 `capabilities/`。新业务不应改内核。

## 配置：8 个 Agent + 1 个 OpenAPI 示例

以下文件均在 `projects/helpdesk/config/`，按场景选择，**不需要全部运行**。

| 文件 | 场景 / 能力 | 用法 |
| --- | --- | --- |
| `agent.yml` | CLI：业务 + knowledge + CLI HITL | main --config 指定它；知识库须先准备 |
| `agent_no_knowledge.yml` | 最轻 CLI：业务工具，无审批 | --offline-demo 用固定模型；真实交互用 --config |
| `agent_api.yml` | 基础 API：业务 + API HITL | ARO_API_CONFIG 指定它，启动 api.app:app；无业务面板/报告 |
| `agent_api_knowledge.yml` | 基础 API + knowledge | 同上；远端 Qdrant 切换说明在文件注释里 |
| `agent_workbench_light.yml` | 轻量工作台：workbench + API HITL + Word | 启动 projects.helpdesk.api:app，不需要知识库依赖 |
| `agent_workbench_experts.yml` | 本次主配置：workbench + API HITL + multiagent | 按下方步骤启动；目录列两专家，知识专家需额外准备 |
| `agent_ticket_expert.yml` | 工单分析子配置：只读查询/历史/重复检查 | experts.py 加载，不单独起服务 |
| `agent_knowledge_expert.yml` | 知识子配置：knowledge + 业务只读工具 | experts.py 加载，不单独起服务 |
| `openapi.yml` | 查询、历史、重复检查的 OpenAPI 3.0 示例 | 供 core.tools.openapi_converter.convert_openapi_to_tools 使用，不会自动导入工具 |

CLI 的 mode: cli HITL 不能直接用于 HTTP。能力、审批和工具白名单以实际启动配置为准。

## 上手步骤

### 1. 最快验证工具闭环

```powershell
.\.venv\Scripts\python.exe -m projects.helpdesk.main --offline-demo
```

使用固定模拟模型查询后退出，无需 API Key/模型权重。想用真实模型交互：

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m projects.helpdesk.main --config projects/helpdesk/config/agent_no_knowledge.yml
```

输入“查询待处理工单”，看 Trace 中的 query_tickets，而不只看助手文字。输入 quit 退出，/new 清空当前上下文。此轻量配置不含 HITL，体验写审批请用下方工作台或完整 CLI 配置。

### 2. 启动专家工作台

```powershell
# 已按根 README 准备 .env 中的模型服务配置
uv pip install --python .venv/Scripts/python.exe -e ".[documents,knowledge]"
$env:ARO_API_CONFIG='projects/helpdesk/config/agent_workbench_experts.yml'
.\.venv\Scripts\python.exe -m uvicorn projects.helpdesk.api:app --host 127.0.0.1 --port 8000
```

另开终端：

```powershell
npm ci
npm run dev -w aro-frontend
```

打开 <http://127.0.0.1:5173/>。服务端设置 ARO_API_TOKEN 时，页面填写同一访问令牌；它不是模型 API Key。构建后由后端托管的方法见根 README。

按顺序体验：

1. 首屏打开“专家列表”：不用先聊天，应列 ticket_analyst 和 knowledge_expert。
2. 点“发起对话”：回到对话区、收起导航、芯片选中目标专家，输入框提示该专家。
3. 向 ticket_analyst 问“查询 TKT-001 的状态”，再问“我刚才关注哪个工单”：专家独立多轮保持上下文。
4. 切“总控助手”，要求“委派工单分析专家查询待处理工单”：由总控调用 delegate_agent。普通问题是否委派由模型决定，并非每问必委派。
5. 总控请求更新工单或 Word 报告：批准后执行，拒绝不得执行。

总控和两位专家平级，各自独立会话 ID、thread、Trace、容量占用及 TTL。切对象会恢复该对象最近未关闭的本地记录；其他记录从历史打开。“新会话”关闭**当前**服务端会话，其他对象会话继续受各自 TTL/关闭管理。

专家只有只读工具，不写工单、不生成报告。专家没有工作台面板，请看 Trace/知识引用。每个 mock 引擎的数据独立，专家快照不能覆盖总控刚更新的事实；要读同一真实源，须在各配置切换相同 HTTP 数据源。

### 3. 准备知识检索

目录读取不启动专家，也不加载权重；真正检索前需要：

- knowledge 可选依赖；BGE-M3 与重排序权重位于 models/knowledge/bge-m3、models/knowledge/bge-reranker-v2-m3，或修改配置中的路径。
- 核对专家配置的 docs_dir、device（默认 cuda，无 GPU 改 cpu）与 Qdrant。
- 审核文档：示例不等于公司制度。没有进程占用该本地 Qdrant 路径时执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.index_helpdesk_docs --config projects/helpdesk/config/agent_knowledge_expert.yml
```

再启动服务，选 knowledge_expert 问“邮箱无法登录怎么办？引用文档”。Trace 应有 search_knowledge_base，回答引用真实来源；缺模型/索引时必须说明失败，不能编造。更新索引后重启服务刷新快照。

API 知识配置和知识专家默认共用 helpdesk_kb 集合、data/qdrant 路径。本地 Qdrant 不能多进程同时打开；远端切换见 agent_api_knowledge.yml 注释，知识专家也须同步存储字段。

## 开发新需求

### 加一个工具

例如“按 ID 查详情”，既有 Repository 已有 get_ticket，无需改契约：

1. 新建 tools/get_ticket.py，参考 tools/query_tickets.py 实现 BaseTool 的 name、description、usage_prompt、permission、schema、run 六项；schema 定义 ticket_id，permission 使用 read。
2. 在 run 中使用既有 Repository，不直接写 HTTP。核心代码如下（GetTicketInput 是你定义的 Pydantic 参数模型）：

```python
async def run(self, **kwargs: Any) -> ToolResult:
    params = GetTicketInput.model_validate(kwargs)
    ticket = await self.repository.get_ticket(params.ticket_id)
    if ticket is None:
        return ToolResult(success=False, error="工单不存在")
    return ToolResult(success=True, output=ticket.model_dump(mode="json"))
```

3. 在 capability.py 导入新类，给 HelpdeskCapability.__init__ 的 _tools 列表增加 GetTicketTool(repository)。workbench 会复用这些工具。
4. 在**实际启动配置** tools 白名单加 get_ticket；要给专家使用，还需加到相应子配置白名单。
5. 补工具/API 测试，运行下方检查并重启服务。写工具声明 write，使用带 HITL 的配置验证批准/拒绝，不能只靠提示词防写入。

### 加一个专家

复制 agent_ticket_expert.yml 为自己的子配置，修改提示词、只读工具及数据源。在工作台配置的 capability_config.multiagent.agents 下增加：

```yaml
- module: projects.helpdesk.experts
  config:
    name: my_expert
    description: 你的只读专家职责及数据来源限定
    config_path: projects/helpdesk/config/agent_my_expert.yml
```

现有 get_agent 工厂已支持这些字段，不需要为每个专家复制 Python 工厂。名称用小写字母开头的 snake_case，不能重复。当前仅支持本地专家直连；专家不启用嵌套委派，只提供 read 工具。重启后目录更新。

### 换公司 HTTP 数据源

在总控与需要同源查询的专家子配置中设置：

```yaml
capability_config:
  helpdesk_tools:
    module: projects.helpdesk.capability  # 主工作台仍用 projects.helpdesk.workbench
    data_source: production
    base_url: https://helpdesk.example/api
    timeout: 10
```

HTTP 适配器使用 GET /tickets（status/priority/category/assignee）、GET /tickets/{id}、PATCH /tickets/{id}、GET /tickets/{id}/history、GET /tickets/duplicates（title/category），返回须匹配 base.py 的模型。

上游不一致时改 repositories/http.py 实现既有契约，不在工具/内核散落 HTTP 请求。适配器尚无通用企业鉴权注入配置；正式接入需设计服务端凭据、身份和上游授权，密钥不能进入模型工具参数。涉及契约或依赖边界变化先按 9.7 提案。

## API 与排错

GET /api/helpdesk/agents 是全局目录，仍需配置要求的 Bearer 认证，不需要 X-ARO-Session。

```json
{"message":"查询待处理工单","session_id":null,"target_agent":"ticket_analyst"}
```

SSE session 事件返回不透明 ID。下一轮传回该 ID 和**同一 target_agent**；总控用 null。各对象分别创建会话，不能自行拼接 ID。错配返回 400，过期返回 404，重叠发送返回 409。审批/关闭使用真实 session_id。

| 现象 | 检查 |
| --- | --- |
| 目录为空 | ARO_API_CONFIG 是否启用 multiagent，后端是否重启 |
| 目录 401 | 页面令牌是否与服务端一致 |
| 会话过期 | 默认 TTL 3600 秒；新建会话。专家历史在发送时验证有效性 |
| 报告失败 | 总控是否启用工作台，documents 依赖、reports 模板及输出目录是否有效 |
| 知识检索失败 | 模型、device、索引集合、Qdrant；不会自动入库 |

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/api/test_workbench.py tests/api/test_workbench_experts.py tests/api/test_expert_direct.py -o addopts=''
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m mypy core capabilities api projects scripts
.\.venv\Scripts\python.exe -m ruff check core capabilities api projects scripts tests
```
