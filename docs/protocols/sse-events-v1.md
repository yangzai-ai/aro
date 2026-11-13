# ARO SSE 事件协议 v1

版本：1.0.0 · 日期：2026-11-13 · 状态：v1 实现协议（本次交付待复验）

## 请求和帧格式

`POST /chat`（兼容 `/api/chat`），Content-Type: application/json，响应 text/event-stream。

```json
{"message":"查询待处理工单","session_id":null,"target_agent":null}
```

首条请求 session_id=null，服务端创建会话并先发 session；后续必须传同一 ID 和同一 target_agent。专家直连传配置中的专家名；null 代表总控自主委派。tenant_id/user_id 不接受浏览器覆盖。没有 createSession 或 GET 聊天订阅接口。

```
event: token
data: {"delta":"你好"}

```

空行分隔事件，data 是 JSON 对象。UTF-8、CRLF/LF、跨网络块和多行 data 必须正确拼接。`: keepalive` 是注释，不是事件。服务端未发送 id，不支持 Last-Event-ID 重放。客户端须忽略未知事件和新增字段。

## 9 种执行事件

下面展示当前字段与可直接作为 data 使用的 JSON。call_id 是不透明字符串，不保证 UUID；parent_call_id 可为 null。工具事件可附带 agent_name/parent_call_id，专家工具的 call_id 带调用前缀，tool_name 带专家命名空间。不要依赖固定工具名。

### 1. token

主 Agent 正文片段；顺序拼接到正文。

```json
{
  "delta": "你好"
}
```

### 2. reasoning

模型实际提供的思考片段；单独展示，不拼入正文、不当作工具证据。并非 DeepSeek 独占；不承诺所有模型都会产生。

```json
{
  "delta": "正在分析"
}
```

### 3. tool_call

工具管线开始，发生在审批之前；不代表工具已执行。

```json
{
  "call_id": "c1",
  "tool_name": "query_tickets",
  "args": {
    "status": "open"
  }
}
```

### 4. tool_result

工具结束，拒绝/超时也返回失败。duration_ms 是管线总耗时，approval_duration_ms 可为 null，execution_duration_ms 不含审批。hook_errors 元素为 {"hook_class":"Observer","hook":"after_tool","error_type":"RuntimeError"}，不是原始异常文本。

```json
{
  "call_id": "c1",
  "tool_name": "query_tickets",
  "args": {
    "status": "open"
  },
  "success": true,
  "result": {
    "tickets": []
  },
  "error": null,
  "duration_ms": 12,
  "approval_duration_ms": null,
  "execution_duration_ms": 11,
  "hook_errors": []
}
```

### 5. agent_start

总控开始委派。

```json
{
  "agent_name": "ticket_analyst",
  "parent_call_id": "c1"
}
```

### 6. agent_done

专家正常结束，trace 为子 Trace，无法生成时可为空对象。

```json
{
  "agent_name": "ticket_analyst",
  "parent_call_id": "c1",
  "trace": {
    "trace_id": "trace-id",
    "thread_id": "session-id",
    "user_input": "查询工单",
    "agent_response": "未查询到工单",
    "tool_calls": [],
    "start_time": 1794528000,
    "end_time": 1794528001,
    "duration_ms": 1000,
    "error": null
  }
}
```

### 7. agent_failed

专家失败，不包含原始异常/凭据。

```json
{
  "agent_name": "ticket_analyst",
  "parent_call_id": "c1"
}
```

### 8. agent_token

专家正文；不要与总控 token 混接。直连专家本身使用普通 token/reasoning。

```json
{
  "agent_name": "ticket_analyst",
  "parent_call_id": "c1",
  "delta": "专家回答"
}
```

### 9. agent_reasoning

专家思考，按 agent_name + parent_call_id 区分来源。

```json
{
  "agent_name": "ticket_analyst",
  "parent_call_id": "c1",
  "delta": "专家思考"
}
```

## 已有 4 种控制和审批事件

这些是当前实际事件，不是未来预留。SDK 同时支持它们。

### session

会话标识，通常为首帧；收到不代表本轮成功。

```json
{
  "session_id": "session-id"
}
```

### approval_required

待授权，expires_at 为 UTC Unix 秒。POST /approve 提交 {session_id,approval_id,approved}；202 只表示决定被接收，不代表工具已成功。

```json
{
  "approval_id": "approval-id",
  "thread_id": "session-id",
  "tool_name": "update_ticket",
  "args": {
    "ticket_id": "TKT-001",
    "status": "resolved"
  },
  "expires_at": 1794528300
}
```

### done

本轮成功终止；trace 可为 null，客户端不能无条件解引用。

```json
{
  "session_id": "session-id",
  "trace": {
    "trace_id": "trace-id",
    "thread_id": "session-id",
    "user_input": "查询工单",
    "agent_response": "未查询到工单",
    "tool_calls": [],
    "start_time": 1794528000,
    "end_time": 1794528001,
    "duration_ms": 1000,
    "error": null
  }
}
```

### error

terminal=false 不是终止（例如审批超时后模型仍会回复）；turn_failed 的 terminal=true 是失败终止。

```json
{
  "code": "approval_timeout",
  "approval_id": "approval-id",
  "message": "Approval timed out; operation rejected",
  "terminal": false
}
```

## 时序和断连

典型：session → reasoning/token → tool_call → [approval_required] → tool_result → token → done。可以多轮工具，也可以没有工具/没有思考；委派事件交错。HTTP 非 2xx 错误在流开始前处理；流中 error 使用 terminal 标识。

断流且未收到 done/terminal=true error 时，执行结果未知；不可自动重发 POST。客户端主动取消也不保证外部业务操作已撤销。没有事件重放、断点续聊或 exactly-once 承诺。只对幂等 GET Trace 查询做有上限的指数退避，401/403/404 不重试。

## Trace 结构与查询

AgentTrace 字段：trace_id、thread_id（可 null）、user_input、agent_response、tool_calls、start_time（UTC Unix 秒）、end_time/duration_ms（可 null）、error（可 null）。tool_calls 含 tool_result 字段及 start_time/end_time。Trace 不含 reasoning 正文。

- GET /sessions/{session_id}/traces?limit=50&offset=0：已结束且当前保留的 Trace，按原顺序；1<=limit<=100、offset>=0。
- GET /traces/{trace_id}：必须带 X-ARO-Session 头；只查询该会话，跨会话/未知/过期返回 404，缺头 422。
- 两者有 /api 别名，启用部署 token 时需 Bearer；使用和 SSE 一致的边界脱敏。OpenAPI 可在 /openapi.json 查询类型。
- HTTP 当前只保留最近一轮，列表通常至多一条。进程重启、会话过期/关闭后丢失；不是历史审计，不返回 current_trace 半成品。

## 安全与脱敏

api/sse.py 在 HTTP 发送前递归脱敏字典/列表：字段名忽略大小写后**精确匹配** api_key/apikey/password/authorization/access_token/connection_string 即替换为 [redacted]；另替换配置的模型 API key、部署 token，以及 URL 中 user:password@。不是字段名包含匹配，不保证任意密钥名/正文敏感业务信息被识别；自定义命名须部署方处理。脱敏不是用户授权，也不是对思考内容的保密承诺。

推送与 Trace 响应以服务端脱敏副本发送，不修改内核 Trace。业务代理负责登录态、会话归属、限流和敏感业务数据策略；部署共享 token 不是用户级授权。部署密钥留在代理，不嵌入公开网页。正文/思考不作为 HTML 注入，不复制到下一轮用户输入；受限模型协议回传属于服务端行为。

## 业务后端透传

下面是应嵌入已有业务 FastAPI 的示意。authorize_session 必须使用业务登录态校验 body.session_id、target_agent，不能只检查浏览器提交了一个 ID。新建会话返回的 session 也要绑定到该登录用户，后续 Trace/approve 代理做同样授权。本例不替业务系统实现身份数据库。

```python
# 业务后端示意：补全自己的 authenticated_user/authorize_session/会话绑定。
@app.post("/api/chat")
async def chat_proxy(request: Request, user=Depends(authenticated_user)):
    body = await request.json()
    await authorize_session(user, body)  # 新建、续聊、专家权限都要校验
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
    upstream = None
    try:
        upstream = await client.send(
            client.build_request("POST", aro_url + "/chat", json=body,
                                 headers={"Authorization": "Bearer " + aro_token}),
            stream=True,
        )
        upstream.raise_for_status()  # 在下游发送 SSE headers 前检查错误
        # 由业务系统实现：持久绑定服务端分配的 ID 与登录用户，失败也走下方清理。
        await bind_session_to_user(user, upstream.headers["X-Session-Id"])
    except BaseException:
        if upstream is not None:
            await upstream.aclose()
        await client.aclose()
        raise

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                # 新会话需增量识别 session 帧或结合上游 X-Session-Id 受信绑定。
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    # authenticated_user/authorize_session/bind_session_to_user 都由业务系统实现。
    return StreamingResponse(relay(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
```

代理应关闭压缩/缓冲，配置足够的读取超时，传播断连以取消上游。不把用户自选的 Authorization 直接传给 ARO，不缓存私有 Trace。不要通过 GET 触发聊天，不依赖示例中不存在的 /sessions/{id}/stream。

## 版本管理

当前为 v1（1.0.0）。新增可选字段/事件向后兼容，消费者忽略未知内容；删除字段、改类型/语义需要新大版本。没有实际 Accept 版本协商或响应版本字段，不发送虚构的版本头。未来若实现聊天续传，需单独设计事件序号、请求幂等及任务所有权。
