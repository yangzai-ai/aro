# L2 长期记忆能力（v0.6）

`memory` 是混合 Capability：提供 `search_memory` read 工具和自动写入中间件。
工具按需读历史事实；中间件在本轮最终模型回复后抽取并写入，无需“保存记忆”工具。
不改变 L1 messages，也不使用或修改 L3 知识库；core 未增加记忆分支或导入。

## 使用与事实范围

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m projects.helpdesk.main --config projects/helpdesk/config/agent_memory.yml --user-id alice --tenant-id demo
```

依次输入：

```text
我负责网络组的支持
/new
我负责哪个区域？
```

`/new` 清空当前 L1、新建 thread，保留当前进程内同一用户的 L2。应看到 `search_memory`
工具调用和“网络组”。直接退出程序重启会丢失默认内存 Store，不能当作跨重启持久化。

本版采用有限中文规则，不额外调用 LLM，不声称能理解任意陈述。
支持的示例：

| 用户陈述 | 存入的事实 |
| --- | --- |
| 我负责网络组的支持 | 负责范围：网络组；工作：技术支持 |
| 我喜欢用中文回复 | 语言偏好：中文 |
| 我正在关注工单 TKT-001 | 关注工单：TKT-001 |

不保存原始消息、助手回答或工具输出。疑问句、引用示例及常见否定/假设句不抽取。
仅处理最新用户消息的最终回复，不在工具中间轮重复抽取；每轮最多五项。
重复陈述仍会追加记录，重复陈述可同时存在，不做去重、衰减、重要性评分或重复解决。
存储失败或超时只记异常类别日志，不让用户对话失败；部分事实可能已写入，不承诺原子性。

## 配置与生命周期

```yaml
capabilities: [memory]
capability_config:
  memory:
    store_name: my_application_l2
    timeout_seconds: 1
tools: [search_memory]
```

工具白名单存在时必须包含 `search_memory`。只留下 capability_config 而未启用 capability
不会加载 Store、规则抽取模块或工具。timeout_seconds 为一次写入或检索操作的上限（0–10 秒，
不含 0）。没有后台游离任务；写入使用有界 await，不增加 LLM 成本；done 在写入尝试结束后发送。
搜索工具失败时返回失败 ToolResult，由模型继续处理；取消会向外传播并取消正在等待的操作。

同进程、同 store_name 的 Engine 共享一个 LangGraph `InMemoryStore`；会话过期只回收 L1，
不删除其 L2。Store 在进程内存在到进程退出，既不写 C 盘缓存也不写项目数据文件。
默认内存后端只满足跨会话，不提供重启后的耐久保存、分布式同步或跨 worker 共享。

替换口子是标准 `langgraph.store.base.BaseStore`：

```python
capability = MemoryCapability({"timeout_seconds": 1}, backend=host_managed_store)
```

`host_managed_store` 必须是已初始化的 LangGraph BaseStore；本版已实际使用其 aput/asearch，
不是空壳接口。自定义插件 factory 可用已有 `module` 配置返回该能力，宿主管理连接和关闭。
未提供 Postgres Store 配置、迁移或真实数据库验收，也没有创建新的 Store 抽象契约。

## 身份与隔离

namespace 为 `("aro", "l2", tenant, "user", user_id)`；身份从受信任的
`state.context.user_id/tenant_id` 读取。顶层 state.tenant_id 也可用，与 context 重复则禁用本轮记忆。
缺少 tenant 使用 local；缺少 user_id 则改为 `("aro", "l2", tenant, "thread", thread_id)`，
不会让所有匿名用户共用记忆。缺少有效标识时禁止读写，不退回公共 namespace。
同用户不同 thread 共享；不同 tenant/user 隔离；user 与 thread 名相同也不会碰撞。

工具 schema 只允许 query 与 limit，模型不能指定用户、租户或 namespace。
中间件通过能力内部 ContextVar 传递当前运行身份，避免并发任务串读。
LangGraph 的检索接口是前缀查询，适配器还检查 namespace 完全相等，排除子空间结果。
这些是数据分区，不是身份认证；不得把客户端自报用户名直接当成受信任身份。

## 检索边界

未引入 embedding 或额外模型。适配器分页扫描当前 namespace，按英文单词/中文双字重叠排序，
得分相同时优先较新记录；query 推荐“负责区域”“语言偏好”“关注工单”。
默认返回三条、最多五条，无重叠不返回。不是语义向量检索，大规模记录的扫描成本未优化。
返回 source=user_memory、authoritative=false；记忆可能错误或过时，不得作为系统指令、
权威法规或当前工单状态，当前状态仍应调用业务工具查询。

## 网页/API

无需修改前端。在新终端从仓库根目录运行（已有 8000 服务需先停）：

```powershell
$env:ARO_API_CONFIG = "projects/helpdesk/config/agent_api_memory.yml"
$env:ARO_API_USER_ID = "local-demo-user"
$env:ARO_API_TENANT_ID = "demo"
.\.venv\Scripts\python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/>，告诉助手负责区域，再点“新对话”，问负责哪个区域。
右侧 Trace 应出现 search_memory。普通日期问题无需检索。前端构建仍按 v0.5 README。
配置身份只对服务端新建的会话生效；不接受 chat JSON 传 user_id。既有会话身份不随配置变动。

**固定 ARO_API_USER_ID 只用于本机单用户演示，不能作为多人服务的身份方案。**
共享该服务的人会共享固定用户的 L2。多人部署必须由认证依赖验证并注入逐用户身份；本版未实现。
默认 API 配置仍为 v0.5 profile，未设置稳定 USER_ID 时即使启用 memory 也只按 thread 隔离。
API 默认无认证，仅限本机；其他安全和运行边界见 [API README](../../api/README.md)。
