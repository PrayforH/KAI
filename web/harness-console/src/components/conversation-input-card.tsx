"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import { formatConversationAnswers, type ConversationInput } from "../lib/conversation-input";
import styles from "./conversation-input-card.module.css";
import { CodexResponseIcon } from "./codex-response-icon";

type Answer = { selected: string[]; text: string };
const emptyAnswer: Answer = { selected: [], text: "" };

export function ConversationInputCard({ input, disabled, onSubmit }: {
  input: ConversationInput;
  disabled: boolean;
  onSubmit: (answer: string) => void | Promise<void>;
}) {
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [questionIndex, setQuestionIndex] = useState(0);
  const [collapsed, setCollapsed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const pending = useRef(false);
  const card = useRef<HTMLElement>(null);
  const question = input.questions[questionIndex];
  const value = answers[question.id] ?? emptyAnswer;
  const answer = formatConversationAnswers(input, answers);
  const locked = disabled || sending || submitted;
  const hasCurrentAnswer = value.selected.length > 0 || value.text.trim().length > 0;
  const lastQuestion = questionIndex === input.questions.length - 1;

  function select(option: string) {
    if (locked) return;
    setAnswers(previous => {
      const current = previous[question.id] ?? emptyAnswer;
      const selected = question.type === "single" ? [option]
        : current.selected.includes(option) ? current.selected.filter(item => item !== option)
        : [...current.selected, option];
      return { ...previous, [question.id]: { ...current, selected } };
    });
  }

  function navigate(index: number) {
    if (sending || index < 0 || index >= input.questions.length) return;
    setQuestionIndex(index);
    card.current?.focus();
  }

  async function submit(skip = false) {
    const response = skip ? formatConversationAnswers(input, answers, true) : answer;
    if (locked || !response || pending.current) return;
    pending.current = true;
    setSending(true);
    setError("");
    try { await onSubmit(response); setSubmitted(true); }
    catch (error) { setError(error instanceof Error ? error.message : "发送失败，请重试"); }
    finally { pending.current = false; setSending(false); }
  }

  function advance() {
    if (locked || !hasCurrentAnswer) return;
    if (lastQuestion) void submit();
    else navigate(questionIndex + 1);
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.defaultPrevented || event.nativeEvent.isComposing || event.repeat
      || event.metaKey || event.ctrlKey || event.altKey || collapsed) return;
    // Keep shortcuts inside the card and leave text editing to the input.
    if (event.target instanceof Element && event.target.closest('input[type="text"], textarea, [contenteditable="true"]')) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      navigate(questionIndex + (event.key === "ArrowRight" ? 1 : -1));
    } else if (!locked && /^[1-8]$/.test(event.key)) {
      const option = question.options[Number(event.key) - 1];
      if (option) { event.preventDefault(); select(option); }
    } else if (event.key === "Enter" && event.target === event.currentTarget) {
      event.preventDefault();
      advance();
    }
  }

  return <section ref={card} className={styles.card} aria-label="待补充的信息" tabIndex={0} onKeyDown={onKeyDown}>
    <header className={styles.header}>
      <button className={styles.heading} type="button" aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}>
        <CodexResponseIcon name="chevronDown" className={collapsed ? styles.chevronCollapsed : undefined} />
        <span>{input.title}</span>
      </button>
      <div className={styles.navigation}>
        {submitted && <span role="status">已提交</span>}
        {input.questions.length > 1 && <>
          <button type="button" aria-label="上一题" disabled={sending || questionIndex === 0} onClick={() => navigate(questionIndex - 1)}><CodexResponseIcon name="chevronDown" className={styles.previous} /></button>
          <span aria-live="polite">{questionIndex + 1} / {input.questions.length}</span>
          <button type="button" aria-label="下一题" disabled={sending || lastQuestion} onClick={() => navigate(questionIndex + 1)}><CodexResponseIcon name="chevronDown" className={styles.next} /></button>
        </>}
      </div>
    </header>
    {!collapsed && <div className={styles.body}>
      <fieldset disabled={locked}>
        <legend>{question.label}</legend>
        {question.options.length > 0 && <div className={styles.options}>
          {question.options.map((option, index) => <label key={option} className={styles.option}>
            <input type={question.type === "multi" ? "checkbox" : "radio"} name={question.id}
              checked={value.selected.includes(option)} onChange={() => select(option)} />
            <span className={styles.marker} aria-hidden="true">{value.selected.includes(option) ? <CodexResponseIcon name="check" /> : index + 1}</span>
            <span className={styles.optionText}>{option}</span>
          </label>)}
        </div>}
        <div className={styles.freeform}>
          <CodexResponseIcon name="pencil" />
          <input className={styles.text} type="text" aria-label={`${question.label} · 填写回答`}
            placeholder={question.type === "text" ? "填写回答" : "或填写其他答案 / 补充说明"}
            maxLength={4000} value={value.text} onChange={event => {
              const text = event.target.value;
              setAnswers(previous => ({ ...previous, [question.id]: { ...(previous[question.id] ?? emptyAnswer), text } }));
            }} />
        </div>
      </fieldset>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {!submitted && <footer className={styles.footer}>
        <span className={styles.hint}>{question.type === "multi" ? "可选择多项" : "选择一项或填写回答"}</span>
        <div className={styles.actions}><button className={styles.skip} type="button" disabled={locked} onClick={() => void submit(true)}>跳过</button>
        <button className={styles.submit} type="button" disabled={locked || (lastQuestion ? !answer : !hasCurrentAnswer)} onClick={advance}>
          {sending ? "发送中…" : lastQuestion ? "提交回答" : "下一步"}<span aria-hidden="true">⏎</span>
        </button></div>
      </footer>}
    </div>}
  </section>;
}
