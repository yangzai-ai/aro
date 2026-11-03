# ARO 智能体工作台

React 18 + TypeScript + Vite，无额外 UI 框架。从本目录执行 `npm ci`、`npm run build`；构建产物由后端提供。`npm run dev` 启动 5173，代理 `/api` 到本机 8000。

在仓库根目录 PowerShell 启动专家工作台（需要既有 LLM 环境配置和 documents 依赖，无需本地知识模型）：

```powershell
$env:ARO_API_CONFIG = 'projects/helpdesk/config/agent_workbench_experts.yml'
.\.venv\Scripts\python.exe -m uvicorn projects.helpdesk.api:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/` 使用构建版，或另开终端从 frontend 运行 `npm run dev` 后打开 5173。使用其他工作台配置时，专家目录可能为空，界面会明确说明。通用 API 入口不提供工单面板或报告下载。

专家工厂位于 `projects/helpdesk/experts.py`，配置启用既有 multiagent/delegate_agent；`GET /api/helpdesk/agents` 要求与面板相同的访问令牌和 X-ARO-Session。工单分析专家仅有查询、历史、重复检查工具，每次委派创建并关闭独立子引擎。示例专家使用独立 mock 快照，不共享主会话更新；真实源可在只读子配置中配置现有 HTTP Repository。主链写操作仍需 HITL。

主题（亮/暗/系统）与业务预设（IT 支持/客户服务/通用）分别保存。所有设计 token 集中在 `src/theme/theme.css`：复制一个 `[data-preset]` token 组，并在 `theme.ts` 和 `index.html` 的允许值中注册键，即可增加皮肤。业务预设改变外观，不改变后端能力、模型或权限。

会话转录与元信息只存本浏览器 localStorage，不跨设备同步；其中可能包含业务内容，请在可信浏览器使用，可从历史面板清除。令牌仅存页面内存。历史始终可查看，继续前验证服务端会话；新建会话关闭当前服务端会话并保留本地转录，过期或关闭后不能继续、下载旧产物。浏览器禁止存储时降级为当前会话并提示。语音按钮仅在支持 Web Speech API 时显示，实际识别需要浏览器权限，识别失败会提示；不支持时仍可键盘输入。

模型切换与附件上传均明确禁用为“规划中”。快捷输入和专家选择只填入输入框，不自动发请求；专家路由仍由 LLM 决定，不保证直连。报告只根据服务端元数据下载，携带认证与会话头，不使用模型编写的地址。
