# v1.0 可选能力

| 目录 | 职责 |
| --- | --- |
| `basic_tools/` | 基础工具 |
| `knowledge/` | 检索、模型与向量库资源 |
| `hitl/` | 人工审批 |
| `memory/` | 长期记忆 |
| `multiagent/` | Agent 注册与委派 |
| `mcp_client/` | 接入外部 MCP Server 的工具 |
| `doc_generation/` | Word 文档生成 |

项目 YAML 选择能力及工具白名单。工具和Hook通过 core 契约贡献给 Engine；业务规则保留在项目层。配置细节参考各能力实现及已有 YAML。OpenAPI 批量接入属于 v1.0 之后的扩展，已移除；v1.0 的基础转换器仍在 `core/tools/`。
