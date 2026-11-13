import type { ToolStep } from '@aro/client';
export interface ToolCallCardProps { step: ToolStep; index?: number; busy?: boolean }
export function ToolCallCard({ step, index = 0, busy = false }: ToolCallCardProps) {
 return <>
        <div className={`step-number ${step.success === undefined ? 'running' : step.success ? 'success' : 'failed'}`}>{index + 1}</div>
        <div className="step-content"><div className="step-title">{step.tool_name}</div>
          <div className="step-status">{step.success === undefined ? (busy ? '执行中 / 等待确认' : '本轮中断，结果未确认') : step.success ? '执行成功' : '未执行或失败'}{step.duration_ms !== undefined && ` · ${step.duration_ms.toFixed(1)} ms`}</div>
          {step.execution_duration_ms !== undefined && <div className="step-status">{step.approval_duration_ms != null && `授权 ${step.approval_duration_ms.toFixed(1)} ms · `}执行 {step.execution_duration_ms.toFixed(1)} ms</div>}
          {!!step.hook_errors?.length && <p className="step-status">观察钩子异常：{step.hook_errors.map(error => `${error.hook_class} (${error.error_type})`).join('、')}</p>}
          <details open={step.success === undefined}><summary>调用参数</summary><pre>{JSON.stringify(step.args, null, 2)}</pre></details>
          {step.success !== undefined && <details><summary>{step.success ? '查看结果' : '查看原因'}</summary><pre>{step.success ? JSON.stringify(step.result, null, 2) : step.error}</pre></details>}
        </div>
 </>;
}
