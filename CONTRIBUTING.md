# 参与 ARO 开发

开发前请完整阅读[工程约定](docs/design/CONVENTIONS.md)，其架构与契约规则为准。版本状态见[路线图](ROADMAP.md)。

## 环境

需要 Python 3.11+、uv；前端需要 Node.js 22 LTS。以下 PowerShell 命令在仓库根目录执行：

```powershell
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev,documents,mcp,knowledge,hitl]"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

按需安装可选能力依赖；知识库依赖体积较大。只运行轻量项目可使用 `.[dev,documents]`。在本地 `.env` 填模型服务配置，勿提交密钥、权重或运行数据。完整启动方法见 [README](README.md)。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check core capabilities api projects scripts tests
.\.venv\Scripts\python.exe -m mypy core capabilities api projects scripts
Set-Location frontend
npm ci
npm run test
npm run build
Set-Location ..
```

真实模型与远端服务测试需要相应依赖、模型和环境配置；按测试声明启用，报告跳过原因。不要用缩短测试范围或削弱安全断言来掩盖失败。

## 代码放在哪里

依赖方向为 `projects → capabilities → core`：项目组合能力，能力只依赖公开契约；core 不反向依赖能力或项目。LangGraph 只能在 `core/kernel/` 内导入。

新增通用能力：在 `capabilities/<name>/` 实现 `Capability`，按需返回 `BaseTool` 工具和 `Middleware` 中间件；需要资源初始化时实现异步 `start/close`。项目 YAML 显式启用，禁用时不得提前加载可选依赖。补充功能、安全隔离与失败清理测试。

新增业务项目：参考 `projects/helpdesk/` 的目录结构，新建 `projects/<name>/`，定义业务 Repository、适配器、工具及项目 Capability，在 `config/` 配置提示词、工具白名单与可选能力。保留写操作审批及业务系统最终校验，不把项目专用分支塞进内核。

三个稳定扩展契约是 `BaseTool / Middleware / Capability`。涉及其签名、语义或装配边界的修改，先按工程约定第 9.7 节提案，获确认再实现。

## 提交

小步提交，每个提交说明问题、改动与验证；保持 main 可运行。功能、测试基线修复和文档清理尽量分开。提交前检查暂存差异，勿纳入 `.env`、`.venv`、模型、data、node_modules、tmp 或生成报告。更新相关文档；未完成能力明确标为规划中，不能提供无效按钮。
