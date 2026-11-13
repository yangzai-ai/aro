import { useEffect, useState } from 'react';
export interface ThinkingBlockProps {
  content: string; collapsed?: boolean; active?: boolean; agent?: string; durationMs?: number;
}
export function ThinkingBlock({content, collapsed = true, active = false, agent, durationMs = 0}: ThinkingBlockProps) {
  const [open, setOpen] = useState(active || !collapsed);
  useEffect(() => { setOpen(active || !collapsed); }, [active, collapsed]);
  if (!content) return null;
  return <details className="reasoning-block" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary title="浏览器从首个思考片段到正文或结束的观察用时，可能包含工具等待，并非模型纯计算时长">思考过程{agent ? ` (${agent})` : ''} · {active ? '正在思考' : '已思考'} {(durationMs / 1000).toFixed(1)} 秒</summary>
    <pre>{content}</pre>
  </details>;
}
