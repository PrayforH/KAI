"use client";
import { useRef, useState } from "react";
import { formatConversationAnswers, type ConversationInput } from "../lib/conversation-input";
import styles from "./conversation-input-card.module.css";

export function ConversationInputCard({ input, disabled, onSubmit }: {
  input: ConversationInput; disabled: boolean; onSubmit: (answer: string) => void | Promise<void>;
}) {
  const [answers, setAnswers] = useState<Record<string, {selected: string[]; text: string}>>({});
  const [collapsed, setCollapsed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const pending = useRef(false);
  const answer = formatConversationAnswers(input, answers);
  const locked = disabled || sending || submitted;
  async function submit() {
    if (locked || !answer || pending.current) return;
    pending.current = true; setSending(true); setError("");
    try { await onSubmit(answer); setSubmitted(true); }
    catch (error) { setError(error instanceof Error ? error.message : "发送失败，请重试"); }
    finally { pending.current = false; setSending(false); }
  }
  return <section className={styles.card} aria-label="待补充的信息">
    <header><button type="button" aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}><svg viewBox="0 0 16 16" aria-hidden="true"><path d={collapsed ? "m6 4 4 4-4 4" : "m4 6 4 4 4-4"} /></svg><strong>{input.title}</strong></button><span>{submitted ? "已提交" : `${input.questions.length} 项`}</span></header>
    {!collapsed && <div className={styles.body}>{input.questions.map(question => {
      const value = answers[question.id] ?? {selected: [], text: ""};
      return <fieldset key={question.id} disabled={locked}><legend>{question.label}</legend>
        {question.options.length > 0 && <div className={styles.options}>{question.options.map(option => <label key={option}><input type={question.type === "multi" ? "checkbox" : "radio"} name={question.id} checked={value.selected.includes(option)} onChange={event => setAnswers(previous => ({...previous, [question.id]: {...value, selected: question.type === "single" ? [option] : event.target.checked ? [...value.selected, option] : value.selected.filter(item => item !== option)}}))} /><span>{option}</span></label>)}</div>}
        <input className={styles.text} type="text" aria-label={`${question.label} · 填写回答`} placeholder={question.type === "text" ? "填写回答" : "或填写其他答案 / 补充说明"} maxLength={4000} value={value.text} onChange={event => setAnswers(previous => ({...previous, [question.id]: {...value, text: event.target.value}}))} />
      </fieldset>;
    })}</div>}
    {error && <p role="alert">{error}</p>}
    {!submitted && <footer><button type="button" disabled={locked || !answer} onClick={() => void submit()}>{sending ? "发送中…" : "提交回答"}</button></footer>}
  </section>;
}
