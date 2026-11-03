export const statuses = { open: '待处理', assigned: '已分配', in_progress: '处理中', resolved: '已解决', closed: '已关闭' } as const;
export interface Ticket {
  id: string; title: string; description: string; category: string; priority: string;
  status: keyof typeof statuses; requester: string; assignee: string | null;
  created_at: string; updated_at: string; resolved_at: string | null;
}
export interface Artifact { id: string; filename: string; expires_at: string; size: number }
interface TicketHistory { ticket_id: string; timestamp: string; action: string; operator: string; details: Record<string, unknown> }
export interface Panel {
  source: string; observed_at: string; tickets: Ticket[]; counts: Record<keyof typeof statuses, number>;
  selected: Ticket | null; history: TicketHistory[] | null;
  duplicate_check: { observed_at: string; result: { query: { title: string; category: string }; duplicates: Ticket[] } } | null;
  artifacts: Artifact[];
}
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const timestamp = (v: unknown): v is string => typeof v === 'string' && Number.isFinite(Date.parse(v));
function ticket(v: unknown): v is Ticket {
  return record(v) && ['id', 'title', 'description', 'category', 'priority', 'requester'].every(k => typeof v[k] === 'string') &&
    typeof v.status === 'string' && Object.hasOwn(statuses, v.status) &&
    (v.assignee === null || typeof v.assignee === 'string') && timestamp(v.created_at) && timestamp(v.updated_at) && (v.resolved_at === null || timestamp(v.resolved_at));
}
export function isPanel(v: unknown): v is Panel {
  if (!record(v) || typeof v.source !== 'string' || !timestamp(v.observed_at) || !Array.isArray(v.tickets) || !v.tickets.every(ticket) ||
    !record(v.counts) || !Object.keys(statuses).every(k => typeof (v.counts as Record<string, unknown>)[k] === 'number' && Number.isInteger((v.counts as Record<string, unknown>)[k]) && Number((v.counts as Record<string, unknown>)[k]) >= 0) ||
    !(v.selected === null || ticket(v.selected))) return false;
  return (v.history === null || Array.isArray(v.history) && v.history.every(h => record(h) && typeof h.ticket_id === 'string' && timestamp(h.timestamp) && typeof h.action === 'string' && typeof h.operator === 'string' && record(h.details))) &&
    (v.duplicate_check === null || record(v.duplicate_check) && timestamp(v.duplicate_check.observed_at) && record(v.duplicate_check.result) && record(v.duplicate_check.result.query) && typeof v.duplicate_check.result.query.title === 'string' && typeof v.duplicate_check.result.query.category === 'string' && Array.isArray(v.duplicate_check.result.duplicates) && v.duplicate_check.result.duplicates.every(ticket)) &&
    Array.isArray(v.artifacts) && v.artifacts.every(a => record(a) && typeof a.id === 'string' && /^[a-f0-9]{48}$/.test(a.id) && typeof a.filename === 'string' && /^report-[a-f0-9]{8}\.docx$/.test(a.filename) && timestamp(a.expires_at) && typeof a.size === 'number' && Number.isFinite(a.size) && a.size > 0);
}
export function TicketPanel({ data, status, busy, onSelect, onAsk, onRefresh }: {
  data: Panel | null; status: string; busy: boolean; onSelect: (id: string) => void;
  onAsk: (text: string) => void; onRefresh: () => void;
}) {
  const selected = data?.selected;
  return <section className="panel ticket-panel" aria-label="工单面板">
    <div className="panel-heading"><h2>工单面板</h2><button className="text-button" onClick={onRefresh}>刷新数据</button></div>
    <div className="panel-body">
      <p className="section-note">{data ? `${data.source === 'mock' ? '模拟数据' : data.source} · 会话数据快照` : status}</p>
      {data && <>
        <div className="stats">{(Object.keys(statuses) as (keyof typeof statuses)[]).map(key => <div key={key}><span>{statuses[key]}</span><strong>{data.counts[key]}</strong></div>)}</div>
        <h3>工单列表 <small>共 {data.tickets.length} 条，按当前数据源返回</small></h3>
        <div className="ticket-list">{data.tickets.map(t => <button key={t.id} className={`ticket ${selected?.id === t.id ? 'selected' : ''}`} onClick={() => onSelect(t.id)} aria-pressed={selected?.id === t.id}>
          <span className="ticket-meta">{t.id}<span>{statuses[t.status]}</span></span><strong>{t.title}</strong><small>{t.category} · {t.priority} · {t.assignee || '未分配'}</small>
        </button>)}</div>
        {selected && <section className="selection-detail"><span className="eyebrow">选中工单 / {selected.id}</span><h3>{selected.title}</h3><p>{selected.description}</p>
          <dl><dt>状态 / 优先级</dt><dd>{statuses[selected.status]} / {selected.priority}</dd><dt>分类 / 请求人</dt><dd>{selected.category} / {selected.requester}</dd><dt>负责人</dt><dd>{selected.assignee || '未分配'}</dd><dt>创建</dt><dd>{new Date(selected.created_at).toLocaleString()}</dd><dt>更新</dt><dd>{new Date(selected.updated_at).toLocaleString()}</dd><dt>解决</dt><dd>{selected.resolved_at ? new Date(selected.resolved_at).toLocaleString() : '尚未解决'}</dd></dl>
          <div className="object-actions"><button disabled={busy} onClick={() => onAsk(`请检测相似工单，标题：${selected.title}，分类：${selected.category}，以工具结果为准。`)}>检测重复</button><button className="secondary" disabled={busy} onClick={() => onAsk(`请为工单 ${selected.id} 生成 Word 状态报告。`)}>请求生成报告</button></div>
          <h4>操作历史</h4>{data.history === null ? <p>历史暂不可用。</p> : data.history.length ? data.history.map((h, i) => <details key={i}><summary>{h.action} · {h.operator} · {new Date(h.timestamp).toLocaleString()}</summary><pre>{JSON.stringify(h.details, null, 2)}</pre></details>) : <p>未返回历史记录。</p>}
          <h4>重复检查证据</h4>{data.duplicate_check ? <><small>检查于 {new Date(data.duplicate_check.observed_at).toLocaleString()}</small><p>查询：{data.duplicate_check.result.query.title} / {data.duplicate_check.result.query.category}</p>{data.duplicate_check.result.duplicates.length ? data.duplicate_check.result.duplicates.map(t => <p key={t.id}>{t.id} · {t.title} · {statuses[t.status]}</p>) : <p>该次检查未返回相似工单。</p>}</> : <p>本会话尚无检查证据，不代表没有重复。</p>}
        </section>}
        <small className="observed">读取于 {new Date(data.observed_at).toLocaleString()} · 非持续监控</small>
      </>}
    </div>
  </section>;
}
