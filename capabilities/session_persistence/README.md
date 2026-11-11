# 会话持久化 Capability（v1.6）

这是使用标准 Capability + Hook 生命周期的参考插件，不提供工具，不要求框架识别插件名。身份校验由 Engine.load_session 负责。它保存运行消息和 context，与 L2 长期记忆不同。

## 安装和配置

Memory 默认零额外依赖；SQLite 和 PostgreSQL 分别按需安装：

```powershell
uv pip install --python .venv/Scripts/python.exe -e ".[session-sqlite]"
# 或
uv pip install --python .venv/Scripts/python.exe -e ".[session-postgres]"
```

项目 YAML 支持映射形式（或旧 capabilities 列表 + capability_config）：

```yaml
capabilities:
  session_persistence:
    backend: sqlite
    sqlite_path: data/sessions/helpdesk.db
    retention_days: 30
    delete_on_session_close: false
```

配置示例：projects/helpdesk/config/agent_session_persistence.yml（只读模拟业务工具，不加载知识模型）。未配置 sqlite_path 时默认 ~/.aro/sessions.db；公司部署建议显式指定受控数据目录。backend 只接受 memory/sqlite/postgres，retention_days 必须大于 0，未知字段拒绝。

PostgreSQL 改 backend 为 postgres，并在服务进程环境变量 ARO_DB_URL 提供连接串。不要把连接串写进模型输入、提示词或提交到仓库。使用专属数据库或 search_path/schema，表名为 sessions；部署账户应有建表和读写权限。驱动按需连接，池大小最多 4，SQL 命令与关闭等待均限制 30 秒。

Entry Point 组 aro.capabilities 注册 session_persistence -> capabilities.session_persistence.capability:get_capability。重新 editable 安装后可通过元数据发现；未安装元数据时仍支持模块约定回退。安装不自动启用。

## 受信会话 ID 的 Engine 恢复

```python
from core.config_loader import AgentConfig, load_llm_config
from core.kernel.engine import AgentEngine
from core.kernel.state import AgentState

config = AgentConfig("projects/helpdesk/config/agent_session_persistence.yml")
engine = await AgentEngine.create(load_llm_config(), config)
try:
    # ID 与身份必须由宿主授权后提供，不直接信任客户端/模型输入。
    initial = AgentState(messages=[], context={
        "thread_id": trusted_thread_id,
        "tenant_id": trusted_tenant_id,
        "user_id": trusted_user_id,
    })
    state = await engine.run("查询待处理工单", initial)
finally:
    await engine.close()
```

关闭后重建 Engine，并在相同存储配置下传相同受信 thread_id/tenant_id/user_id，即可继续上下文。SQLite/PostgreSQL 能跨进程；Memory 为每个 backend 实例私有字典，新建实例或进程后不恢复。切换 backend 只切换存储选择，不自动搬迁数据。

当前 API 对不存在/过期的 session_id 仍返回 404，也不会自动从存储重建浏览器会话。CLI /new 仍创建新 ID；这里的重启恢复示例面向宿主代码，不是浏览器跨重启续聊功能。持久化也不会保存/恢复 MockRepository 的业务数据。

## 生命周期与关闭语义

- Capability.initialize 同步校验配置并选择 backend，不打开连接；支持 Entry Point 类/工厂约定。
- Hook.on_start 初始化连接并原子删除存储中已过期记录。后端还在每次 load 时按 updated_at 检查有效期；没有后台定时删除任务。list_expired 可查询过期 ID，delete 幂等。
- Hook.on_session_load 读取快照，用 LangChain messages_from_dict 恢复。Engine 校验消息结构及身份冲突；插件不复制身份校验逻辑。
- Hook.before 无操作，after 每个图内模型轮结束写快照；会保存中间工具轮已记录的结果，不是整段用户请求的最终事务提交。异常/断连可能已有已完成轮快照，不能宣称“取消等于未执行/未保存”。
- 保存键只来自 core.kernel.context.get_thread_id()。Engine 在图调用前用 bind_thread_id 绑定，异常/取消后 reset；图内异步任务继承该作用域。脱离 Engine 直接调用 after 而没有绑定时明确报错，不悄悄漏存。
- 默认 on_session_close 保留记录；Engine.close 代表结束运行，不代表永久删除。delete_on_session_close=true 才删除，此模式不能正常关闭后恢复，API 过期/退出也会通知删除。
- Hook.on_stop 关闭连接；Capability.close 是幂等兜底，覆盖装配失败/部分启动。SQLite worker 与 PostgreSQL pool 在取消清理时也要收尾。

## 消息完整性与悬空调用

快照版本为 1，保存 LangChain messages_to_dict 的完整结构、context 以及顶层 tenant_id（若有）。包括 additional_kwargs.reasoning_content、tool_calls、ToolMessage 结果。reasoning 不拼进 content。无法 JSON 编码的数据拒绝保存，不用 str/repr 静默降级。

恢复时，每个 AI 工具批次中缺少对应结果的调用，在下一条非 ToolMessage 前补 status=error 的 ToolMessage：

> 因服务中断，此工具调用未记录结果，执行状态未知；未自动重放，请先查询业务系统核实。

保留已有结果，按原调用顺序补缺失结果，不执行工具。同批重复/空 ID、孤立或重复结果、名称不匹配会拒绝恢复，不猜测关联。不同批次可复用 ID。没有检查点续跑、审批恢复或自动重试。

## 存储与运维边界

SQLite/PostgreSQL 使用参数化 upsert/load/delete。updated_at 是 UTC Unix 秒数（SQLite REAL/PostgreSQL DOUBLE PRECISION），避免时区解释差异；默认保留 30 天，以最后一次 save 为准。JSON 与 reasoning 是敏感运行数据，部署方负责目录/数据库权限、备份、加密与租户访问授权。

相同 thread_id 必须由宿主统一授权与互斥；本插件不实现跨进程并发会话锁，多个进程同时写同一 ID 为后写覆盖，不可把它当多 worker 协调器。数据库初始化时依据当前实例 retention_days 原子清理该 sessions 表的过期记录，同库不同应用应分 schema/表空间并使用一致保留策略。

## 验证

```powershell
uv pip install --python .venv/Scripts/python.exe -e ".[dev,session-sqlite,session-postgres]"
.\.venv\Scripts\python.exe -m pytest tests/capabilities/session_persistence tests/core/kernel/test_thread_context.py -q
```

普通测试使用真实 SQLite 和 PostgreSQL 驱动替身。若需要真实 PostgreSQL 测试，设置 ARO_TEST_SESSION_POSTGRES_URL 为专用测试库连接串，再运行 test_postgres_live.py；测试会创建随机独立 schema 并在 finally 删除，测试账号须有 CREATE SCHEMA 权限。它不会默认连接生产 ARO_DB_URL。没有测试连接时明确 skip，不算真实数据库通过。
