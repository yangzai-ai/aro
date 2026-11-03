import { useEffect, useState } from 'react';
import type { Approval } from '../client/types';

interface Props { approval: Approval; sending: boolean; onDecision: (approved: boolean) => void }

export function ApprovalCard({ approval, sending, onDecision }: Props) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const remaining = Math.max(0, Math.ceil(approval.expires_at - now / 1000));
  return <section className="approval-card" role="region" aria-label="待审批操作" aria-live="polite">
    <div className="approval-heading"><span className="approval-icon" aria-hidden="true">!</span><div><h3>需要你的确认</h3><p>操作尚未执行，请核对以下参数。</p></div><span className="countdown">{remaining}s</span></div>
    <strong className="approval-tool">{approval.tool_name}</strong>
    <pre>{JSON.stringify(approval.args, null, 2)}</pre>
    <div className="approval-actions"><button className="secondary" disabled={sending || !remaining} onClick={() => onDecision(false)}>取消本次操作</button><button disabled={sending || !remaining} onClick={() => onDecision(true)}>{sending ? '正在提交…' : '确认执行'}</button></div>
    <small>{remaining ? '确认后执行所示操作；取消本次操作将保持业务数据不变。' : '审批已过期，等待服务端结束本次操作。'}</small>
  </section>;
}
