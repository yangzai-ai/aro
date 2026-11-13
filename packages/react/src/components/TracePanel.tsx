import type { AgentTrace, ToolStep } from '@aro/client';
import { ToolCallCard } from './ToolCallCard.js';

export interface TracePanelProps { steps?: ToolStep[]; trace: AgentTrace | null; busy?: boolean }

export function TracePanel({ trace, steps = trace?.tool_calls ?? [], busy = false }: TracePanelProps) {
  return <section className="trace-panel panel" aria-label="Agent Trace">
    <div className="panel-heading"><h2>执行记录</h2><span>{steps.length} 个步骤</span></div>
    <div className="trace-body">
      <p className="section-note">实时查看工具调用与结果</p>
      {!steps.length && <p className="trace-empty">{busy ? '正在生成回复，工具调用会在此显示。' : '发送消息后，执行步骤会显示在这里。'}</p>}
      <ol className="steps">{steps.map((step, index) => <li key={step.call_id}>
        <ToolCallCard step={step} index={index} busy={busy} />
      </li>)}</ol>
      {trace && <div className="trace-summary"><strong>本轮完成</strong><span>{trace.duration_ms?.toFixed(0) ?? '—'} ms</span><small>Trace {trace.trace_id}</small></div>}
    </div>
    <div className="trace-footnote">结果以实际工具执行记录为准</div>
  </section>;
}
