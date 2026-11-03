import type { AgentTrace, ToolStep } from '../client/types';

interface Props { steps: ToolStep[]; trace: AgentTrace | null; busy: boolean }

export function TracePanel({ steps, trace, busy }: Props) {
  return <section className="trace-panel panel" aria-label="Agent Trace">
    <div className="panel-heading"><h2>执行记录</h2><span>{steps.length} 个步骤</span></div>
    <div className="trace-body">
      <p className="section-note">实时查看工具调用与结果</p>
      {!steps.length && <p className="trace-empty">{busy ? '正在生成回复，工具调用会在此显示。' : '发送消息后，执行步骤会显示在这里。'}</p>}
      <ol className="steps">{steps.map((step, index) => <li key={step.call_id}>
        <div className={`step-number ${step.success === undefined ? 'running' : step.success ? 'success' : 'failed'}`}>{index + 1}</div>
        <div className="step-content"><div className="step-title">{step.tool_name}</div>
          <div className="step-status">{step.success === undefined ? (busy ? '执行中 / 等待确认' : '本轮中断，结果未确认') : step.success ? '执行成功' : '未执行或失败'}{step.duration_ms !== undefined && ` · ${step.duration_ms.toFixed(1)} ms`}</div>
          <details open={step.success === undefined}><summary>调用参数</summary><pre>{JSON.stringify(step.args, null, 2)}</pre></details>
          {step.success !== undefined && <details><summary>{step.success ? '查看结果' : '查看原因'}</summary><pre>{step.success ? JSON.stringify(step.result, null, 2) : step.error}</pre></details>}
        </div>
      </li>)}</ol>
      {trace && <div className="trace-summary"><strong>本轮完成</strong><span>{trace.duration_ms.toFixed(0)} ms</span><small>Trace {trace.trace_id}</small></div>}
    </div>
    <div className="trace-footnote">结果以实际工具执行记录为准</div>
  </section>;
}
