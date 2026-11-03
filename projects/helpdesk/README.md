# IT Helpdesk Project

演示 IT 工单系统业务。

**启动工作台**（轻量无知识库）：
```powershell
$env:ARO_API_CONFIG='projects/helpdesk/config/agent_workbench_light.yml'
.\.venv\Scripts\python.exe -m uvicorn projects.helpdesk.api:app --host 127.0.0.1 --port 8000
```

**CLI 启动**（离线演示）：
```powershell
.\.venv\Scripts\python.exe -m projects.helpdesk.main --offline-demo
```

当前为模拟工单数据，用于演示框架能力。
