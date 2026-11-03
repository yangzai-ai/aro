# MCP Client（v0.8）

可选工具来源，使用官方 `langchain-mcp-adapters` 自动发现外部 tools，包装为标准
BaseTool。KernelLoop、LLM、Trace、HITL 不识别 MCP 协议。

## 配置

```yaml
capabilities: [mcp_client, hitl]
capability_config:
  hitl:
    mode: cli  # API 使用 api
  mcp_client:
    servers:
      fs:
        transport: stdio
        command: node
        args:
          - /absolute/path/to/your-mcp-server/index.js
          - /absolute/path/to/allowed-directory
        discovery_timeout_seconds: 15
        call_timeout_seconds: 30
        permissions:
          read_text_file: read
          # 其他工具默认 write；可显式设 forbidden
```

从仓库根目录启动。command、args、可选 cwd 和 env 均由受信服务端配置提供，
不允许聊天内容修改。env 中可使用 adapter 支持的 `${VARIABLE}` 引用进程环境变量，
密钥放环境变量，不放 YAML。v0.8 只支持 stdio，不实现 HTTP/SSE/OAuth。

服务器标识以小写字母开头、仅小写字母和数字、最长 16 字符，最多 8 个 server。
工具名为 `mcp_<server>__<remote_tool>`，保留 `mcp_` 命名空间给本能力。
最终名称须符合 `[A-Za-z0-9_-]{1,64}`，非法元数据使该 server 降级，不能静默改名。
不同 server 的同名工具彼此隔离；本地工具不要使用保留前缀。
AgentConfig 若有非空 tools 白名单，须列入完整的 MCP 工具名；不设置则自动注册。

权限覆盖使用**远端原名**。协议的 readOnlyHint 等 annotations 不作为权限依据：
未覆盖的工具一律 write。未知覆盖名称记警告，工具继续采用保守默认权限。
forbidden 被 Engine 从可调用列表移除；没有 HITL 时，write 被既有内核拒绝执行。

## 生命周期与失败

使用 `await AgentEngine.create(settings, config)`，结束时 `await engine.close()`。
启用本能力时调用旧同步 `AgentEngine(...)` 会明确报错，不会悄悄漏掉工具。

启动时逐 server 发现，默认每个 15 秒，可设大于 0、至多 120 秒。
失败/超时/adapter 未安装只记 server 标识和异常类型，该 server 不贡献工具；
其他能力和 server 仍可用。格式错误的本地配置直接报错。
超时取消会等待 SDK 清理，总耗时可超过配置的 I/O 超时；server 越多启动越久。

依据 adapter 0.2.2 实际接口，发现连接在 get_tools 返回前关闭，
每次工具调用自行创建并关闭会话/stdio 子进程，不保留跨调用协议状态。
退出、超时和取消沿 adapter/SDK 的上下文退出路径清理；无健康检查、重试、重连或进程池。
close 使包装器失效；上层须先取消并等待进行中的 run（API 已处理），再关闭 Engine。

参数 JSON Schema 保留并在调用前验证，错误参数不触达 server。
ToolCall envelope 保留 ToolMessage artifact 中的 structured_content；
文本/内容块作为 output，结构化结果同时带上 content 和 structured_content。
MCP isError、连接异常和调用超时成为失败 ToolResult；取消向上传播。
异常文本不进入日志或 LLM，日志仅有工具名/异常类型。
写超时不能推断未执行，须检查目标系统后决定下一步；框架不自动重试。

## 安装与检查

```powershell
uv pip install --python .venv/Scripts/python.exe -e ".[mcp]"
.\.venv\Scripts\pytest.exe tests/capabilities/mcp_client tests/test_async_lifecycle.py tests/api/test_mcp.py -q
```

已验证 adapter 0.2.2 + MCP Python SDK 1.30.0。SDK 约束为 >=1.30,<2，
因为 adapter 0.2.x 调用的 SDK 1.x transport API 在 2.x 已移除。
不启用能力时不 import 本模块、adapter 或 MCP SDK。

完整使用：filesystem 演示（历史资料已移出仓库）。
交接：v0.8（历史资料已移出仓库）。
