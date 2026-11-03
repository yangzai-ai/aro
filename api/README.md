# HTTP API

通用入口：`api.app:app`，负责聊天、会话、SSE、审批与认证。项目工作台入口：`projects.helpdesk.api:app`，增加面板与报告下载。

配置由 `api/settings.py` 定义，默认读取 `.env` 中的 `ARO_API_*`。当前默认 Agent YAML 依赖 helpdesk。启动与部署条件见[运行说明](../docs/operations.md)和[仓库 README](../README.md)。

保留会话容量限制、同会话互斥、超时和取消清理。当前会话存储在进程内；使用单 worker。共享 Bearer 令牌不是完整的业务用户授权。
