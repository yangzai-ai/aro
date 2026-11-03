# 可选 Word 模板能力

安装 `uv pip install --python .venv/Scripts/python.exe -e ".[documents]"`。
标准配置入口：

```yaml
capabilities: [doc_generation, hitl]
tools: [render_word_template]
capability_config:
  doc_generation:
    templates:
      example: path/to/trusted.docx
    output_dir: data/reports
    ttl_seconds: 1800
  hitl:
    mode: api
```

使用 `await AgentEngine.create(...)`，start 验证配置模板；sync构造会明确拒绝异步能力。
`render_word_template` 为 write，通过标准HITL后接受 template_id 和字符串 data；模板ID来自服务端白名单，不能传路径或模板代码。
本能力只做通用渲染，不负责业务事实真实性；业务工作台不暴露这个通用工具，而由项目服务端验证事实，见 v1.0交接（历史资料已移出仓库）。

`public.py` 是获批项目组合的最小类型化接口：DocumentConfig、RenderRequest、Artifact、ArtifactContent、DocumentService 的 start/render/list_artifacts/read/close。
每服务实例独立随机namespace，默认1800秒/最多20份。产物惰性过期，关闭清理；取消先等待同步工作线程实际结束，不保证强杀进程后的磁盘回收。
独立能力不自动新增HTTP路由；项目负责认证、真实会话归属、元数据交付与下载组合。
不要从其他能力横向import本实现，也不要同时让两个owner关闭同一实例。
