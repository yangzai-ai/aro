import { useEffect, useRef, useState, type FormEvent } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatMessage } from '../client/types';
import type { Expert } from './Sidebar';
import { VoiceButton } from './VoiceButton';
import { presets, type Preset } from '../theme/theme';

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  readOnly?: boolean;
  status: string;
  preset: Preset;
  suggestion: { text: string; key: number } | null;
  onExperts: () => void;
  experts: Expert[];
  selectedExpert: string | null;
  onSelectExpert: (name: string | null) => void;
}

export function ChatWindow({ messages, busy, onSend, onStop, readOnly, status, preset, suggestion, onExperts, experts, selectedExpert, onSelectExpert }: Props) {
  const [draft, setDraft] = useState('');
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [messages]);
  useEffect(() => { if (suggestion) { setDraft(suggestion.text); document.getElementById('message')?.focus(); } }, [suggestion]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!draft.trim() || busy || readOnly) return;
    onSend(draft.trim());
    setDraft('');
  };
  return <section className="chat-panel panel" aria-label="对话窗口">
    <div className="panel-heading"><div><span className="eyebrow">工作空间 / {presets[preset]}</span><h2>与 ARO 一起，把问题解决</h2></div><span role="status">{busy ? '执行中' : status}</span></div>
    <div className="messages" aria-live="polite" aria-relevant="additions text">
      {!messages.length && <div className="empty-state">
        <div className="assistant-mark">A<span className="spark">✦</span></div><span className="eyebrow">思考 · 协作 · 执行</span><h3>你好，今天想解决什么？</h3>
        <p>用自然语言提出问题，工具执行留下证据。<br />涉及写入的操作，始终由你确认。</p>
      </div>}
      {messages.map(message => <article className={`message ${message.role}`} key={message.id}>
        <div className="message-label">{message.role === 'user' ? '你' : selectedExpert || '总控助手'}</div>
        <div className="message-text">{message.text ? <Markdown skipHtml remarkPlugins={[remarkGfm]} components={{a: ({children}) => <span>{children}</span>, img: ({alt}) => <span>{alt}</span>}}>{message.text}</Markdown> : (busy ? '正在思考或等待工具结果…' : '本轮没有文本回复。')}</div>
      </article>)}
      <div ref={bottom} />
    </div>
    <form onSubmit={submit} className="composer">
      <div className="expert-selector" role="group" aria-label="对话对象">
        <button type="button" className={`chip ${selectedExpert === null ? 'selected' : ''}`} aria-pressed={selectedExpert === null} disabled={busy} onClick={() => onSelectExpert(null)}>总控助手</button>
        {experts.map(expert => <button type="button" key={expert.name} className={`chip ${selectedExpert === expert.name ? 'selected' : ''}`} aria-pressed={selectedExpert === expert.name} title={expert.description} disabled={busy} onClick={() => onSelectExpert(expert.name)}>{expert.name}</button>)}
      </div>
      <div className="quick-input" aria-label="快捷输入">{['查询待处理工单', '查看 TKT-001 的操作历史', '检查标题“无法登录邮箱系统”、分类 account 的重复工单'].map(text => <button type="button" className="chip" key={text} onClick={() => { setDraft(text); document.getElementById('message')?.focus(); }} disabled={busy || readOnly}>{text}</button>)}</div>
      <label className="sr-only" htmlFor="message">消息</label>
      <textarea id="message" placeholder={readOnly ? '此记录暂不能继续，请新建会话。' : selectedExpert ? `向 ${selectedExpert} 提问…` : '描述你的问题，或告诉我下一步要做什么…'} value={draft} onChange={event => setDraft(event.target.value)} rows={3} disabled={busy || readOnly} maxLength={10000} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); submit(event); }
      }} />
      <div className="composer-tools"><div className="tool-group"><VoiceButton disabled={busy || !!readOnly} onText={text => setDraft(old => (old + text).slice(0, 10000))} /><button type="button" className="secondary" disabled title="后端暂不接收附件">附件 · 规划中</button><button type="button" className="secondary" onClick={onExperts}>专家目录</button></div><label className="model-placeholder"><span className="sr-only">模型切换</span><select disabled aria-label="模型切换 · 规划中"><option>模型切换 · 规划中</option></select></label></div>
      <div className="composer-footer"><small>Enter 发送 · Shift + Enter 换行<br />{readOnly ? status : '以实际工具证据为准，重要操作请核对。'}</small>{busy ? <button type="button" onClick={onStop} className="secondary">停止生成 ■</button> : <button type="submit" disabled={!draft.trim() || readOnly}>发送 <span aria-hidden="true">↑</span></button>}</div>
    </form>
  </section>;
}
