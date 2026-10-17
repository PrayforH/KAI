"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { formatConversationAnswers, isSensitiveQuestion, type ConversationInput } from "../lib/conversation-input";
import styles from "./conversation-input-card.module.css";
import { collectConversationSecrets, redactConversationText } from "../lib/conversation-secrets";
import { useConversationRedactor } from "./conversation-redaction";
import { CodexResponseIcon } from "./codex-response-icon";

import { VoiceInput } from "./voice-input";
import { useDictationComposer } from "./use-dictation-composer";

type Answer = { selected: string[]; text: string };
export const FOLLOWUP_IDLE_MS = 5 * 60 * 1000;
const emptyAnswer: Answer = { selected: [], text: "" };
const OPTIONS_PER_PAGE = 8;

export function ConversationInputCard({ input, disabled, onSubmit, modelRoute = "", onVoiceActive, onDormant }: {
  input: ConversationInput;
  onDormant?: () => void;
  modelRoute?: string;
  onVoiceActive?: (active: boolean) => void;
  disabled: boolean;
  onSubmit: (answer: string) => void | Promise<void>;
}) {
  const redact = useConversationRedactor();
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [questionIndex, setQuestionIndex] = useState(0);
  const [optionPages, setOptionPages] = useState<Record<string, number>>({});
  const [collapsed, setCollapsed] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [voiceActive, setVoiceActive] = useState(false);
  const textInput = useRef<HTMLInputElement>(null);
  const composing = useRef(false);
  const pending = useRef(false);
  const card = useRef<HTMLElement>(null);
  const question = input.questions[questionIndex];
  const value = answers[question.id] ?? emptyAnswer;
  const pageCount = Math.ceil(question.options.length / OPTIONS_PER_PAGE);
  const optionPage = Math.min(optionPages[question.id] ?? 0, Math.max(0, pageCount - 1));
  const optionOffset = optionPage * OPTIONS_PER_PAGE;
  const visibleOptions = question.options.slice(optionOffset, optionOffset + OPTIONS_PER_PAGE);
  const sensitive = isSensitiveQuestion(question);
  function writeText(text: string) {
    setAnswers(previous => ({ ...previous, [question.id]: { ...(previous[question.id] ?? emptyAnswer), text: text.slice(0, 4000) } }));
  }
  const dictation = useDictationComposer(textInput, () => value.text, writeText, composing);
  const answer = formatConversationAnswers(input, answers);
  const locked = disabled || sending || submitted;
  const dormantCallback = useRef(onDormant);
  dormantCallback.current = onDormant;
  const idleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const touch = useCallback(() => {
    if (idleTimer.current !== undefined) clearTimeout(idleTimer.current);
    if (locked || voiceActive || collapsed) return;
    idleTimer.current = setTimeout(() => { setCollapsed(true); dormantCallback.current?.(); }, FOLLOWUP_IDLE_MS);
  }, [locked, voiceActive, collapsed]);
  useEffect(() => { touch(); return () => { if (idleTimer.current !== undefined) clearTimeout(idleTimer.current); }; }, [touch]);
  const hasCurrentAnswer = value.selected.length > 0 || value.text.trim().length > 0;
  const lastQuestion = questionIndex === input.questions.length - 1;

  function select(option: string) {
    if (locked || voiceActive) return;
    setAnswers(previous => {
      const current = previous[question.id] ?? emptyAnswer;
      const selected = question.type === "single" ? [option]
        : current.selected.includes(option) ? current.selected.filter(item => item !== option)
        : [...current.selected, option];
      return { ...previous, [question.id]: { ...current, selected } };
    });
  }

  function navigate(index: number) {
    if (sending || voiceActive || index < 0 || index >= input.questions.length) return;
    setQuestionIndex(index);
    card.current?.focus();
  }

  function navigateOptions(page: number) {
    if (locked || voiceActive || page < 0 || page >= pageCount) return;
    setOptionPages(previous => ({ ...previous, [question.id]: page }));
    card.current?.focus();
  }

  async function submit(skip = false) {
    const response = skip ? formatConversationAnswers(input, answers, true) : answer;
    if (locked || voiceActive || !response || pending.current) return;
    pending.current = true;
    setSending(true);
    setError("");
    try { await onSubmit(response); setSubmitted(true);
      setAnswers(previous => Object.fromEntries(Object.entries(previous).map(([id, answer]) => [id,
        input.questions.some(q => q.id === id && isSensitiveQuestion(q)) ? {...answer, text: "", selected: []} : answer])));
    }
    catch (error) { setError(redact(redactConversationText(error instanceof Error ? error.message : "发送失败，请重试", collectConversationSecrets([response])))); }
    finally { pending.current = false; setSending(false); }
  }

  function advance() {
    if (locked || voiceActive || !hasCurrentAnswer) return;
    if (lastQuestion) void submit();
    else navigate(questionIndex + 1);
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.defaultPrevented || event.nativeEvent.isComposing || event.repeat
      || event.metaKey || event.ctrlKey || event.altKey || collapsed || voiceActive) return;
    // Keep shortcuts inside the card and leave text editing to the input.
    if (event.target instanceof Element && event.target.closest('input[type="text"], input[type="password"], textarea, [contenteditable="true"]')) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      navigate(questionIndex + (event.key === "ArrowRight" ? 1 : -1));
    } else if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      navigateOptions(optionPage + (event.key === "PageDown" ? 1 : -1));
    } else if (!locked && /^[1-8]$/.test(event.key)) {
      const option = visibleOptions[Number(event.key) - 1];
      if (option) { event.preventDefault(); select(option); }
    } else if (event.key === "Enter" && event.target === event.currentTarget) {
      event.preventDefault();
      advance();
    }
  }

  return <section ref={card} className={styles.card} aria-label="待补充的信息" tabIndex={0} onKeyDown={onKeyDown}
    onPointerDownCapture={touch} onKeyDownCapture={touch} onFocusCapture={touch} onInputCapture={touch}>
    <header className={styles.header}>
      <button className={styles.heading} type="button" aria-expanded={!collapsed} disabled={voiceActive} onClick={() => setCollapsed(!collapsed)}>
        <CodexResponseIcon name="chevronDown" className={collapsed ? styles.chevronCollapsed : undefined} />
        <span>{redact(input.title)}</span>
      </button>
      <div className={styles.navigation}>
        {submitted && <span role="status">已提交</span>}
        {input.questions.length > 1 && <>
          <button type="button" aria-label="上一题" disabled={sending || voiceActive || questionIndex === 0} onClick={() => navigate(questionIndex - 1)}><CodexResponseIcon name="chevronDown" className={styles.previous} /></button>
          <span aria-live="polite">{questionIndex + 1} / {input.questions.length}</span>
          <button type="button" aria-label="下一题" disabled={sending || voiceActive || lastQuestion} onClick={() => navigate(questionIndex + 1)}><CodexResponseIcon name="chevronDown" className={styles.next} /></button>
        </>}
      </div>
    </header>
    {!collapsed && <div className={styles.body}>
      <fieldset disabled={locked}>
        <legend>{redact(question.label)}</legend>
        {question.options.length > 0 && <div className={styles.options}>
          {visibleOptions.map((option, index) => <label key={option} className={styles.option}>
            <input type={question.type === "multi" ? "checkbox" : "radio"} name={question.id}
              disabled={voiceActive} checked={value.selected.includes(option)} onChange={() => select(option)} />
            <span className={styles.marker} aria-hidden="true">{value.selected.includes(option) ? <CodexResponseIcon name="check" /> : index + 1}</span>
            <span className={styles.optionText}>{redact(option)}</span>
          </label>)}
        </div>}
        {pageCount > 1 && <nav className={styles.optionPagination} aria-label="选项分页">
          <span aria-live="polite">选项 {optionOffset + 1}–{optionOffset + visibleOptions.length} / {question.options.length}
            {value.selected.length > 0 && ` · 已选 ${value.selected.length} 项`}</span>
          <div>
            <button type="button" aria-label="上一组选项" disabled={locked || voiceActive || optionPage === 0} onClick={() => navigateOptions(optionPage - 1)}>上一页</button>
            <button type="button" aria-label="下一组选项" disabled={locked || voiceActive || optionPage + 1 >= pageCount} onClick={() => navigateOptions(optionPage + 1)}>下一页</button>
          </div>
        </nav>}
        <div className={styles.freeform}>
          <CodexResponseIcon name="pencil" />
          <input ref={textInput} className={styles.text} type={sensitive ? "password" : "text"}
            autoComplete="off" spellCheck={!sensitive} autoCapitalize="none" autoCorrect="off" aria-label={`${redact(question.label)} · 填写回答`}
            placeholder={sensitive ? (submitted ? "已提交，内容已隐藏" : "填写敏感信息") : question.type === "text" ? "填写回答" : "或填写其他答案 / 补充说明"}
            onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
            maxLength={4000} value={value.text} onChange={event => {
              const text = event.target.value;
              dictation.onChange(text); writeText(text);
            }} />
          {!sensitive && <VoiceInput key={question.id} placement="inline" disabled={locked} modelRoute={modelRoute}
            onDraft={dictation.onDraft} onInsert={dictation.onInsert}
            onActive={active => { dictation.onActive(active); setVoiceActive(active); onVoiceActive?.(active); }} />}
        </div>
      </fieldset>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {!submitted && <footer className={styles.footer}>
        <span className={styles.hint}>{question.type === "multi" ? "可选择多项" : "选择一项或填写回答"}</span>
        <div className={styles.actions}><button className={styles.skip} type="button" disabled={locked || voiceActive} onClick={() => void submit(true)}>跳过</button>
        <button className={styles.submit} type="button" disabled={locked || voiceActive || (lastQuestion ? !answer : !hasCurrentAnswer)} onClick={advance}>
          {sending ? "发送中…" : lastQuestion ? "提交回答" : "下一步"}<span aria-hidden="true">⏎</span>
        </button></div>
      </footer>}
    </div>}
  </section>;
}
