import { useEffect, useRef, useState } from 'react';

interface SpeechResult { isFinal: boolean; 0: { transcript: string } }
interface SpeechEvent { resultIndex: number; results: { length: number; [index: number]: SpeechResult } }
interface Recognition {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((event: SpeechEvent) => void) | null; onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null; start: () => void; stop: () => void; abort: () => void;
}
type SpeechWindow = Window & { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
export function VoiceButton({ disabled, onText }: { disabled: boolean; onText: (text: string) => void }) {
  const Constructor = (window as SpeechWindow).SpeechRecognition || (window as SpeechWindow).webkitSpeechRecognition;
  const current = useRef<Recognition | null>(null);
  const callback = useRef(onText); callback.current = onText;
  const [listening, setListening] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => () => { if (current.current) { current.current.onend = null; current.current.onresult = null; current.current.onerror = null; current.current.abort(); } }, []);
  useEffect(() => { if (disabled) current.current?.abort(); }, [disabled]);
  if (!Constructor) return null;
  const toggle = () => {
    if (listening) { current.current?.stop(); return; }
    const recognition = new Constructor(); current.current = recognition;
    recognition.lang = 'zh-CN'; recognition.continuous = false; recognition.interimResults = false;
    recognition.onresult = event => {
      for (let i = event.resultIndex; i < event.results.length; i++) if (event.results[i].isFinal) callback.current(event.results[i][0].transcript);
    };
    recognition.onerror = event => { setError(`语音输入失败（${event.error}），请使用键盘输入。`); setListening(false); };
    recognition.onend = () => { setListening(false); current.current = null; };
    try { setError(''); recognition.start(); setListening(true); } catch { setError('无法启动语音输入，请检查浏览器权限。'); }
  };
  return <><button type="button" className="secondary" disabled={disabled} aria-pressed={listening} aria-label={listening ? '停止语音输入' : '开始语音输入'} onClick={toggle}>{listening ? '■ 结束录音' : '◉ 语音'}</button>{error && <small role="status">{error}</small>}</>;
}
