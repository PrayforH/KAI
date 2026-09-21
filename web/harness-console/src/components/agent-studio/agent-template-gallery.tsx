"use client";
import { useState } from "react";
import { AGENT_TEMPLATES, type AgentTemplate } from "../../lib/agent-templates";
import styles from "./agenta-workspace.module.css";
export function AgentTemplateGallery({
  busy,
  error,
  onSelect,
  onBlank,
  onClose,
}: {
  busy: boolean;
  error: string;
  onSelect: (template: AgentTemplate) => void;
  onBlank: () => void;
  onClose: () => void;
}) {
  const [category, setCategory] = useState("全部");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<AgentTemplate | null>(null);
  return (
    <section className={styles.templateGallery} aria-label="从模板创建智能体">
      <header className={styles.templateHeading}>
        <div>
          <small>AGENT TEMPLATES</small>
          <h2>{selected ? selected.name : "从一个模板开始"}</h2>
          <p>
            {selected
              ? selected.description
              : "选择常见工作场景，配置后在 Playground 中试运行。"}
          </p>
        </div>
        <button disabled={busy} onClick={onClose} aria-label="关闭模板目录">
          ×
        </button>
      </header>
      {selected ? (
        <div className={styles.templateDetail}>
          <button disabled={busy} onClick={() => setSelected(null)}>
            ← 所有模板
          </button>
          <div className={styles.templateDetailGrid}>
            <article>
              <h3>工作方式</h3>
              <ol>
                {selected.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              <h3>输入材料</h3>
              <p>{selected.input}</p>
              <h3>输出结果</h3>
              <p>{selected.output}</p>
              <h3>示例任务</h3>
              <blockquote>{selected.example}</blockquote>
            </article>
            <aside>
              <h3>创建配置</h3>
              <dl>
                <dt>模型</dt>
                <dd>使用平台默认路由，创建后可调整</dd>
                <dt>工具</dt>
                <dd>平台基础工具；创建后可调整权限和外部集成</dd>
                <dt>自动运行</dt>
                <dd>创建后手动配置触发器</dd>
                <dt>交付形式</dt>
                <dd>可编辑的个人草稿</dd>
              </dl>
              <button
                className={styles.templatePrimary}
                disabled={busy}
                onClick={() => onSelect(selected)}
              >
                {busy ? "正在创建…" : "使用此模板"}
              </button>
              <p>
                包含任务指令和测试输入，可继续添加 Skill 或导出 DeepAgents。
              </p>
            </aside>
          </div>
        </div>
      ) : (
        <>
          <div className={styles.templateToolbar}>
            <nav aria-label="模板分类">
              {[
                "全部",
                ...new Set(AGENT_TEMPLATES.map((item) => item.category)),
              ].map((item) => (
                <button
                  key={item}
                  aria-pressed={category === item}
                  onClick={() => setCategory(item)}
                >
                  {item}
                </button>
              ))}
            </nav>
            <input
              type="search"
              aria-label="搜索模板"
              placeholder="搜索模板"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className={styles.templateCards}>
            <button
              className={styles.blankTemplate}
              disabled={busy}
              onClick={onBlank}
            >
              <span>＋</span>
              <strong>从空白开始</strong>
              <p>描述工作需求，与构建助手一起设计智能体。</p>
            </button>
            {AGENT_TEMPLATES.filter(
              (item) =>
                (category === "全部" || item.category === category) &&
                `${item.name} ${item.description}`.includes(query),
            ).map((item) => (
              <button
                key={item.id}
                disabled={busy}
                onClick={() => setSelected(item)}
              >
                <small>{item.category}</small>
                <strong>{item.name}</strong>
                <p>{item.description}</p>
                <span>查看模板 →</span>
              </button>
            ))}
          </div>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
