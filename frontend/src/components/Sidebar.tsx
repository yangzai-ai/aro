import type { Conversation } from '../history/storage';
export interface Expert { name: string; description: string }
export function Sidebar({ tab, onTab, records, active, onOpen, onNew, disabled, resetting, experts, status, onExpert, onClear }: {
  tab: 'history' | 'experts'; onTab: (tab: 'history' | 'experts') => void;
  records: Conversation[]; active: string | null; onOpen: (record: Conversation) => void; onNew: () => void;
  disabled: boolean; resetting: boolean; experts: Expert[]; status: string; onExpert: (expert: Expert) => void; onClear: () => void;
}) {
  return <><div className="sidebar-heading"><span className="eyebrow">你的工作空间</span><button className="new-conversation" onClick={onNew} disabled={resetting}>＋ 新建会话 <span>↗</span></button></div>
    <div className="tabs" role="tablist" aria-label="导航内容"><button id="history-tab" role="tab" aria-selected={tab === 'history'} aria-controls="sidebar-content" onClick={() => onTab('history')}>会话历史</button><button id="experts-tab" role="tab" aria-selected={tab === 'experts'} aria-controls="sidebar-content" onClick={() => onTab('experts')}>专家列表</button></div>
    <div className="sidebar-content" id="sidebar-content" role="tabpanel" aria-labelledby={`${tab}-tab`}>
      {tab === 'history' ? <><p className="section-note">只保存在本浏览器。新建会话会关闭当前服务端会话，历史仍可查看。</p>{!records.length && <p className="sidebar-empty">还没有会话记录。<br />从右侧开始第一次对话。</p>}{records.map(record => <button className={`history-item ${record.id === active ? 'selected' : ''}`} key={record.id} disabled={disabled} onClick={() => onOpen(record)} aria-current={record.id === active ? 'page' : undefined}><strong>{record.title}</strong><small>{new Date(record.updated).toLocaleString()} · {record.closed ? '仅查看' : '打开时验证有效性'}</small></button>)}{!!records.length && <button className="text-button" disabled={disabled} onClick={onClear}>清除本地历史记录</button>}</> : <><p className="section-note">选择专家直接对话；选择总控助手则由总控自主决定是否委派。各会话上下文独立。</p>{status && <p role="status" className="sidebar-empty">{status}</p>}{experts.map(expert => <article className="expert-card" key={expert.name}><span className="expert-avatar" aria-hidden="true">✦</span><h3>{expert.name}</h3><p>{expert.description}</p><button className="secondary" disabled={disabled} onClick={() => onExpert(expert)}>发起对话 ↗</button></article>)}</>}
    </div><div className="sidebar-foot"><span className="brand-icon small">A</span><div><strong>ARO 智能体工作台</strong><small>工具有证据，执行有边界</small></div></div></>;
}
