# Hook 命名统一验证

日期：2026-11-10。范围：纯重命名，不改变执行逻辑。

## 命名与兼容

- 核心契约移至 `core/kernel/hook.py::Hook`，能力提供方法为 `hooks()`。
- 实现统一为 TraceHook、HITLHook、MemoryWriteHook、DelegationHook；测试替身统一为 SpyHook/MockHook。
- HITL、Memory 的实现模块及相关测试文件改为 hook 命名；装配、API、业务项目、前端类型和文档全部同步。
- `core/kernel/middleware.py` 保留 `Middleware = Hook`，两者为同一个类；别名计划 v1.6 删除。仅保留这个显式旧类导入入口，不新增旧方法/属性/构造参数兼容层。
- Trace/SSE 的 `middleware_errors` 更名为 `hook_errors`，诊断项的类名字段更名为 `hook_class`，保留 `hook` 表示 before/after 等调用阶段，避免重名覆盖；前端同步使用新字段。

## 验证证据

- AST 对比覆盖 147 个原跟踪 Python 模块：旧源码按相同映射机械重命名后，与当前源码完全一致（仅忽略顶层 import 顺序）。没有业务逻辑改动。
- 实际导入验证：旧入口的 Middleware 与新入口的 Hook 对象身份一致。
- 全量 pytest：317 passed、2 skipped、4 warnings，115.71s。
- mypy：Success: no issues found in 90 source files。
- Ruff：All checks passed。
- 前端：npm test 16 passed；npm run build 成功。
- git diff --check：通过。

工作树原有 `docs/v1.5-universal-assembly-brief.md` 删除及未跟踪的 `docs/v1.5-session-persistence-brief.md` 保留原样，不纳入本次提交。未跟踪简报中的旧术语也保留原样；仓库跟踪内容除兼容别名文件外无旧术语。验证输出位于忽略的 tmp/hook-rename-pytest.log。
