# v1.0 部署与配置

所有命令从仓库根目录执行。主环境 `.venv/` 不属于发布代码，部署机器需自行安装依赖。

## 配置位置

| 配置 | 位置 |
| --- | --- |
| 模型 API 地址、密钥、默认模型 | `.env`，参考 `.env.example` |
| Agent 提示词、工具白名单、能力配置 | 业务项目的 YAML；`ARO_API_CONFIG` 选择路径 |
| API 访问令牌、会话容量、TTL、执行超时 | `ARO_API_*` 环境变量，定义见 `api/settings.py` |
| 业务数据源 | `projects/helpdesk/capability.py` 中的项目配置；真实源须实现匹配的业务 HTTP 协议 |
| 检索模型、向量库、集合 | YAML 中 `capability_config.knowledge` |
| Word 模板与输出目录 | YAML 中工作台的 `reports` 配置 |

`api.app:app` 提供聊天、审批和 SSE；`projects.helpdesk.api:app` 在其上增加业务面板与报告下载。当前默认 API 配置仍指向业务项目。保留它是为维持 v1.0 可运行性，业务规则与模拟数据并非通用框架功能。

## 可选依赖

基础依赖见 `pyproject.toml`。按需要安装 `.[documents]`、`.[knowledge]`、`.[mcp]`、`.[hitl]`；`dev` 仅用于维护验证。不要将检索模型权重或虚拟环境打进源码包。

启用知识库前配置已有模型目录（例如 `models/knowledge/bge-m3`、`models/knowledge/bge-reranker-v2-m3`）、业务文档与 Qdrant。入库使用：

```powershell
.\.venv\Scripts\python.exe -m scripts.index_helpdesk_docs --config projects/helpdesk/config/agent_api_knowledge.yml
```

该配置中的样本文档不能当作公司制度或现行法规。换成经业务确认的资料再入库；入库后重启服务更新检索快照。本地 Qdrant 同一路径不能由多个进程同时打开；需要独立服务时按 agent_api_knowledge.yml 内注释切换到远端 Qdrant。模型与索引路径均相对仓库根目录解析。

## 数据、认证与进程

`.env` 不提交；对公司网络开放前设置 `ARO_API_TOKEN` 并部署访问控制。共享访问令牌不等于用户授权。`ARO_API_USER_ID` 是服务端可信身份输入，不能把所有人的请求绑定同一个固定身份，否则会共享长期记忆。

当前会话状态和互斥在进程内维护，使用单 worker；不要直接扩大 workers 并假设会话共享。写工具保留 HITL 审批，拒绝或超时不得当作执行成功。报告通过服务端会话鉴权下载，不相信模型生成的文件地址。

`data/` 保留业务运行数据、报告和索引，`models/` 保留本地权重；迁移部署时按实际配置备份。不要因为整理代码而删除运行数据。源码部署排除 `.git/`、`.venv/`、各类缓存、测试覆盖率产物和 `frontend/node_modules/`；前端部署使用重新构建的 `frontend/dist/`。

测试可使用 `.\.venv\Scripts\python.exe -m pytest -o addopts=''`。外部模型、数据库和真实业务系统联调需按部署环境另行验证；自动化回归通过不代表生产系统已验收。
