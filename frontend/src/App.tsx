import { useEffect, useRef, useState } from 'react';
import { HTTPError, projectRequest, streamChat, submitApproval } from './client/api';
import type { AgentEvent, AgentTrace, Approval, ChatMessage, ToolStep } from './client/types';
import { ApprovalCard } from './components/ApprovalCard';
import { ChatWindow } from './components/ChatWindow';
import { TracePanel } from './components/TracePanel';
import { TicketPanel, isPanel, type Panel, type Artifact } from './components/TicketPanel';
import { Sidebar, type Expert } from './components/Sidebar';
import { readHistory, persistHistory, sessionValue, type Conversation } from './history/storage';
import { presets, useTheme, type Theme, type Preset } from './theme/theme';

export default function App() {
  const [initial] = useState(() => {
    const loaded = readHistory();
    const record = loaded.records.find(r => r.id === sessionValue('aro.active'));
    return { ...loaded, record };
  });
  const [records, setRecords] = useState(initial.records);
  const [storageAvailable, setStorageAvailable] = useState(initial.available);
  const [active, setActive] = useState<string | null>(initial.record?.id || null);
  const [messages, setMessages] = useState<ChatMessage[]>(initial.record?.messages || []);
  const { theme, preset, saved, setTheme, setPreset } = useTheme();
  const [navOpen, setNavOpen] = useState(() => window.innerWidth >= 800);
  const [evidenceOpen, setEvidenceOpen] = useState(() => window.innerWidth >= 1200);
  useEffect(() => {
    const resize = () => {
      if (window.innerWidth < 800) setNavOpen(false);
      if (window.innerWidth < 1200) setEvidenceOpen(false);
    };
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, []);
  const [tab, setTab] = useState<'history' | 'experts'>('history');
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (evidenceOpen) { setEvidenceOpen(false); document.querySelector<HTMLButtonElement>('[aria-controls="evidence"]')?.focus(); }
      else if (navOpen) { setNavOpen(false); document.querySelector<HTMLButtonElement>('[aria-controls="navigation"]')?.focus(); }
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [navOpen, evidenceOpen]);
  const [evidenceTab, setEvidenceTab] = useState('tickets');
  const [suggestion, setSuggestion] = useState<{ text: string; key: number } | null>(null);
  const [availability, setAvailability] = useState<'new' | 'checking' | 'live' | 'expired' | 'unavailable'>(initial.record ? initial.record.targetAgent ? 'live' : 'checking' : 'new');
  const [selectedExpert, setSelectedExpert] = useState<string | null>(initial.record?.targetAgent || null);
  const [experts, setExperts] = useState<Expert[]>([]);
  const [expertStatus, setExpertStatus] = useState('正在读取当前配置的专家目录。');
  const expertController = useRef<AbortController | null>(null);
  const recordPreset = useRef<Preset>(initial.record?.preset || preset);
  const recordsRef = useRef(records); recordsRef.current = records;
  const storeRecords = (next: Conversation[]) => { recordsRef.current = next; setRecords(next); if (!persistHistory(next)) setStorageAvailable(false); };
  useEffect(() => {
    if (!active || !messages.length) return;
    const previous = recordsRef.current.find(r => r.id === active);
    const entry: Conversation = { id: active, sessionId: sessionId.current, title: messages.find(m => m.role === 'user')?.text.slice(0, 32) || '新会话', updated: Date.now(), preset: recordPreset.current, targetAgent: selectedExpert, messages, closed: previous?.closed || false };
    storeRecords([entry, ...recordsRef.current.filter(r => r.id !== active)]);
  }, [messages, active]);
  const [steps, setSteps] = useState<ToolStep[]>([]);
  const [trace, setTrace] = useState<AgentTrace | null>(null);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [busy, setBusy] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState('');
  const [token, setToken] = useState('');
  const [health, setHealth] = useState('连接中');
  const [panel, setPanel] = useState<Panel | null>(null);
  const [panelStatus, setPanelStatus] = useState('尚未建立会话。发送问题后加载。');
  const selection = useRef('');
  const generation = useRef(0);
  const panelController = useRef<AbortController | null>(null);
  const [resetting, setResetting] = useState(false);
  const [downloading, setDownloading] = useState('');
  const controller = useRef<AbortController | null>(null);
  const sessionId = useRef<string | null>(initial.record && !initial.record.closed ? initial.record.sessionId : null);
  const refresh = async (identity = sessionId.current, epoch = generation.current, id = selection.current, target = selectedExpert) => {
    panelController.current?.abort();
    const current = new AbortController(); panelController.current = current;
    if (!identity) { setPanel(null); setPanelStatus('尚未建立有效会话，无法读取工单快照。'); return; }
    if (target) { setPanel(null); setPanelStatus('专家直连会话不提供工作台面板；请查看本轮 Trace 和知识引用。会话有效性在发送时校验。'); return; }
    setPanelStatus('正在读取会话数据…');
    try {
      const result: unknown = await (await projectRequest(`panel${id ? `?selected=${encodeURIComponent(id)}` : ''}`, identity, token, 'GET', current.signal)).json();
      if (!isPanel(result)) throw new Error('工单数据格式不正确');
      if (epoch === generation.current && identity === sessionId.current && !current.signal.aborted) { setPanel(result); setPanelStatus(''); setAvailability('live'); }
    } catch (cause) { if (!current.signal.aborted && epoch === generation.current) { setPanel(null); setPanelStatus(cause instanceof Error ? cause.message : '工单读取失败'); setAvailability(cause instanceof HTTPError && cause.status === 404 ? 'expired' : 'unavailable'); } }
  };
  const refreshExperts = async () => {
    expertController.current?.abort();
    const current = new AbortController(); expertController.current = current;
    setExperts([]);
    setExpertStatus('正在读取专家目录…');
    try {
      const result: unknown = await (await projectRequest('agents', '', token, 'GET', current.signal)).json();
      if (!Array.isArray(result) || !result.every(e => e && typeof e.name === 'string' && typeof e.description === 'string')) throw new Error('专家目录格式不正确');
      if (!current.signal.aborted) { setExperts(result); setExpertStatus(result.length ? '' : '当前配置未启用专家智能体'); }
    } catch (cause) { if (!current.signal.aborted) { setExperts([]); setExpertStatus(cause instanceof Error ? cause.message : '专家目录读取失败'); } }
  };
  useEffect(() => {
    fetch('/api/health').then(response => { if (!response.ok) throw new Error(); setHealth('服务已连接'); }).catch(() => setHealth('服务未连接'));
    if (initial.record && (initial.record.closed || !initial.record.sessionId)) setAvailability('expired'); else { void refresh(); }
    return () => { controller.current?.abort(); panelController.current?.abort(); expertController.current?.abort(); };
  }, []);

  useEffect(() => { void refreshExperts(); return () => expertController.current?.abort(); }, [token]);

  const send = async (text: string) => {
    if (controller.current || resetting || !['new', 'live'].includes(availability)) return;
    if (!active) { const id = crypto.randomUUID(); setActive(id); sessionValue('aro.active', id); recordPreset.current = preset; }
    const epoch = generation.current;
    const current = new AbortController(); controller.current = current;
    const assistantId = crypto.randomUUID();
    setBusy(true); setError(''); setSteps([]); setTrace(null); setApproval(null);
    setMessages(old => [...old, { id: crypto.randomUUID(), role: 'user', text }, { id: assistantId, role: 'assistant', text: '' }]);
    const receive = (event: AgentEvent) => {
      if (epoch !== generation.current) return;
      switch (event.type) {
        case 'session': sessionId.current = event.data.session_id; sessionValue('aro.session', event.data.session_id); setAvailability('live'); setMessages(old => [...old]); void refresh(); void refreshExperts(); break;
        case 'token': setMessages(old => old.map(message => message.id === assistantId ? { ...message, text: message.text + event.data.delta } : message)); break;
        case 'tool_call': setSteps(old => [...old, event.data]); break;
        case 'tool_result': setSteps(old => old.map(step => step.call_id === event.data.call_id ? event.data : step)); setApproval(null); void refresh(); break;
        case 'approval_required': setApproval(event.data); break;
        case 'done': setTrace(event.data.trace); setApproval(null); break;
        case 'error': setError(event.data.message); if (event.data.terminal || event.data.code === 'approval_timeout') setApproval(null); break;
      }
    };
    try { await streamChat(text, sessionId.current, token, receive, current.signal, selectedExpert); }
    catch (cause) { if (epoch === generation.current) { if (cause instanceof HTTPError && cause.status === 404) setAvailability('expired'); setError(current.signal.aborted ? '本轮已停止。已执行的业务操作不会自动撤销。' : cause instanceof Error ? cause.message : '连接失败'); } }
    finally { if (controller.current === current) controller.current = null; if (epoch === generation.current) { setBusy(false); setApproval(null); setDeciding(false); void refresh(); } }
  };

  const decide = async (approved: boolean) => {
    if (!approval || !sessionId.current || deciding) return;
    const epoch = generation.current;
    setDeciding(true); setError('');
    try { await submitApproval(sessionId.current, approval.approval_id, approved, token); if (epoch === generation.current) setApproval(null); }
    catch (cause) { if (epoch === generation.current) setError(cause instanceof Error ? cause.message : '审批提交失败'); }
    finally { if (epoch === generation.current) setDeciding(false); }
  };

  const reset = async () => {
    const old = sessionId.current;
    generation.current++; controller.current?.abort(); controller.current = null; panelController.current?.abort(); expertController.current?.abort();
    sessionId.current = null; sessionValue('aro.session', null); sessionValue('aro.active', null); selection.current = '';
    if (active) storeRecords(recordsRef.current.map(r => r.id === active ? { ...r, closed: true } : r));
    setActive(null); setSelectedExpert(null); setAvailability('new'); setDeciding(false); setExperts([]); setExpertStatus('正在读取当前配置的专家目录。'); setSuggestion(null);
    setPanel(null); setMessages([]); setSteps([]); setTrace(null); setApproval(null); setError(''); setBusy(false); setDownloading(''); setResetting(true); setPanelStatus('正在关闭旧会话…');
    try { if (old) await projectRequest('session', old, token, 'DELETE'); }
    catch (cause) { if (!(cause instanceof HTTPError && cause.status === 404)) setError(`旧会话关闭未确认：${cause instanceof Error ? cause.message : '连接失败'}。旧会话仍受服务端有效期限制。`); }
    finally { setResetting(false); setPanelStatus('新会话尚未建立。发送问题后加载。'); void refreshExperts(); }
  };
  const download = async (a: Artifact) => {
    const identity = sessionId.current, epoch = generation.current;
    if (!identity) return;
    setDownloading(a.id);
    try {
      const blob = await (await projectRequest(`artifacts/${encodeURIComponent(a.id)}`, identity, token)).blob();
      if (epoch !== generation.current) return;
      const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href = url; link.download = a.filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { if (epoch === generation.current) setError(cause instanceof Error ? cause.message : '下载失败'); }
    finally { if (epoch === generation.current) setDownloading(''); }
  };
  const sources = steps.flatMap(step => {
    if (!step.success || step.tool_name !== 'search_knowledge_base' || !step.result || typeof step.result !== 'object') return [];
    const results = (step.result as { results?: unknown }).results;
    if (!Array.isArray(results)) return [];
    return results.filter((r): r is { source: string; heading: string; content: string } => r && typeof r.source === 'string' && typeof r.heading === 'string' && typeof r.content === 'string');
  });
  const openHistory = (record: Conversation) => {
    if (busy || resetting) return;
    generation.current++; panelController.current?.abort();
    selection.current = ''; setPanel(null); setSteps([]); setTrace(null); setApproval(null); setDownloading(''); setError(''); setSuggestion(null);
    setSelectedExpert(record.targetAgent || null);
    setActive(record.id); sessionValue('aro.active', record.id); setMessages(record.messages); recordPreset.current = record.preset;
    sessionId.current = record.closed ? null : record.sessionId;
    setAvailability(record.closed || !record.sessionId ? 'expired' : 'checking');
    setPanelStatus('正在验证服务端会话…');
    if (!record.closed && record.sessionId) { if (record.targetAgent) { setAvailability('live'); setPanelStatus('专家直连会话将在发送时校验；不提供工作台面板。'); } else { void refresh(record.sessionId, generation.current, '', null); } }
    else { setPanelStatus('会话已关闭或过期，仅可查看记录。'); }
    if (window.innerWidth < 800) setNavOpen(false);
  };
  const selectExpert = (name: string | null) => {
    if (busy || resetting) return;
    setTab('history'); setNavOpen(false);
    if (name === selectedExpert) return;
    const existing = recordsRef.current.find(r => !r.closed && (r.targetAgent || null) === name);
    if (existing) { openHistory(existing); return; }
    generation.current++; panelController.current?.abort();
    sessionId.current = null; sessionValue('aro.active', null); sessionValue('aro.session', null);
    setSelectedExpert(name); setActive(null); setAvailability('new'); setMessages([]); setPanel(null); setSteps([]); setTrace(null); setApproval(null); setError(''); setDownloading(''); setSuggestion(null); selection.current = '';
    setPanelStatus(name ? '向该专家发送问题后建立独立会话。' : '向总控发送问题后建立会话。');
  };
  const changeToken = (value: string) => {
    generation.current++; panelController.current?.abort(); expertController.current?.abort();
    sessionId.current = null; sessionValue('aro.active', null); sessionValue('aro.session', null);
    setActive(null); setSelectedExpert(null); setAvailability('new'); setMessages([]); setPanel(null); setSteps([]); setTrace(null); setApproval(null); setExperts([]); setDownloading(''); setSuggestion(null); selection.current = '';
    setPanelStatus('令牌已更改，发送问题建立新会话。'); setExpertStatus('发送问题建立会话后读取专家。'); setToken(value);
  };
  const stateLabel = resetting ? '正在关闭旧会话' : availability === 'expired' ? '会话已过期或关闭，仅可查看记录' : availability === 'checking' ? '正在验证会话' : availability === 'unavailable' ? '会话暂不可用，请刷新验证' : '准备就绪';
  return <div className={`app-shell ${navOpen ? 'nav-open' : ''} ${evidenceOpen ? 'evidence-open' : ''}`}>
    <header className="topbar"><div className="brand"><button className="icon-button" aria-label={navOpen ? '折叠左导航' : '展开左导航'} aria-expanded={navOpen} aria-controls="navigation" onClick={() => setNavOpen(!navOpen)}>☰</button><span className="brand-icon">A</span><div><h1>ARO <span>智能体工作台</span></h1><small>让想法成为行动</small></div></div>
      <div className="header-actions"><span className={`connection ${health === '服务已连接' ? 'online' : ''}`} role="status">{health}</span><label><span className="sr-only">业务预设</span><select aria-label="业务预设" value={preset} onChange={e => setPreset(e.target.value as Preset)}>{Object.entries(presets).map(([key, title]) => <option value={key} key={key}>{title}</option>)}</select></label><label><span className="sr-only">主题</span><select aria-label="主题" value={theme} onChange={e => setTheme(e.target.value as Theme)}><option value="light">亮色</option><option value="dark">暗色</option><option value="system">跟随系统</option></select></label><button className="secondary" aria-label="新建会话" disabled={resetting} onClick={() => void reset()}>＋ 新会话</button><button className="secondary" aria-expanded={evidenceOpen} aria-controls="evidence" onClick={() => setEvidenceOpen(!evidenceOpen)}>{evidenceOpen ? '收起证据' : '查看证据'}</button></div>
    </header>
    <div className="workspace">
      <aside id="navigation" className="navigation" aria-label="会话导航" hidden={!navOpen}><button className="drawer-close text-button" onClick={() => setNavOpen(false)}>关闭导航 ×</button><Sidebar tab={tab} onTab={setTab} records={records} active={active} onOpen={openHistory} onNew={() => void reset()} disabled={busy || resetting} resetting={resetting} experts={experts} status={expertStatus} onExpert={e => selectExpert(e.name)} onClear={() => { if (window.confirm('清除本浏览器的会话转录？这不会关闭其他服务端会话。')) { storeRecords([]); setActive(null); sessionValue('aro.active', null); } }} /></aside>
      <main className="conversation-column"><div className="workspace-meta"><span><i /> 可验证的智能协作</span><details className="token-settings"><summary>访问令牌（可选）</summary><label>仅服务端启用认证时填写<input aria-label="访问令牌" type="password" autoComplete="off" placeholder="仅在本页内存使用" value={token} onChange={e => changeToken(e.target.value)} disabled={busy || resetting} /></label></details></div>
        {(!storageAvailable || !saved) && <div className="notice" role="status">浏览器存储不可用，仅当前会话有效；刷新后设置或记录可能丢失。</div>}
        {error && <div role="alert" className="error-banner">{error}<button aria-label="关闭提示" onClick={() => setError('')}>×</button></div>}
        {approval && <ApprovalCard approval={approval} sending={deciding} onDecision={decide} />}
        <ChatWindow key={active || 'new'} messages={messages} busy={busy} onSend={text => void send(text)} onStop={() => controller.current?.abort()} readOnly={resetting || !['new', 'live'].includes(availability)} status={stateLabel} preset={preset} suggestion={suggestion} experts={experts} selectedExpert={selectedExpert} onSelectExpert={selectExpert} onExperts={() => {setTab('experts'); setNavOpen(true);}} />
        <footer className="conversation-footer">ARO / Agent Runtime & Orchestration <span>来源与状态，以工具证据为准</span></footer>
      </main>
      <aside id="evidence" className="evidence-column" aria-label="执行证据" hidden={!evidenceOpen}><div className="evidence-heading"><div><span className="eyebrow">有据可查</span><h2>执行证据</h2></div><button className="icon-button" aria-label="关闭证据区" onClick={() => setEvidenceOpen(false)}>×</button></div><div className="tabs evidence-tabs" role="tablist" aria-label="证据类型">{[['tickets','工单'],['trace','Trace'],['sources','引用'],['reports','报告']].map(([key,title]) => <button key={key} role="tab" id={`evidence-${key}`} aria-controls="evidence-content" aria-selected={evidenceTab === key} onClick={() => setEvidenceTab(key)}>{title}</button>)}</div>
        <div id="evidence-content" role="tabpanel" aria-labelledby={`evidence-${evidenceTab}`} className="evidence-content">
          {evidenceTab === 'tickets' && (selectedExpert ? <section className="panel panel-body"><h3>专家直连</h3><p className="section-note">此专家没有工作台面板。请查看本轮 Trace 与知识引用；更新工单及生成报告请切换总控助手。</p></section> : <TicketPanel data={panel} status={panelStatus} busy={busy || resetting || availability !== 'live'} onSelect={id => { selection.current = id; setPanel(null); void refresh(sessionId.current, generation.current, id); }} onAsk={text => setSuggestion({text, key: Date.now()})} onRefresh={() => { void refresh(); void refreshExperts(); }} />)}
          {evidenceTab === 'trace' && <TracePanel steps={steps} trace={trace} busy={busy} />}
          {evidenceTab === 'sources' && <section className="panel panel-body sources"><h3>本轮知识引用</h3><p className="section-note">文档是证据而非指令；适用范围以原文与工具返回为准。</p>{!sources.length && <p className="empty-note">本轮尚无检索证据。未检索不代表知识库没有相关内容。</p>}{sources.map((s,i) => <details key={`${s.source}-${i}`}><summary>{s.source} · {s.heading}</summary><p>{s.content}</p></details>)}</section>}
          {evidenceTab === 'reports' && <section className="panel panel-body artifact-panel"><h3>本会话报告</h3><p className="section-note">已执行操作不因停止回复而撤销。报告在会话关闭或到期后不可下载。</p>{!panel?.artifacts.length && <p className="empty-note">尚无报告产物。请选中工单，再请求生成报告。</p>}{panel?.artifacts.map(a => <div className="artifact" key={a.id}><div><b>Word 工单状态报告</b><small>{a.filename} · {(a.size/1024).toFixed(0)} KB<br />到期 {new Date(a.expires_at).toLocaleString()}</small></div><button disabled={!!downloading} onClick={() => void download(a)}>{downloading === a.id ? '下载中…' : '下载 Word'}</button></div>)}</section>}
        </div>
      </aside>
    </div>
  </div>;
}
