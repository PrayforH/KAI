"use client";
import Link from "next/link";
import type { ProjectSpaceState } from "./use-project-space";
import { projectEmpty, size } from "./project-space-shared";
import styles from "./project-home.module.css";

export function ProjectResources({ space }: { space: ProjectSpaceState }) {
  const { resources, setDialog, project, knowledgeNames, skillNames } = space;
  if (!project) return null;
  if (!resources) return null;
  return (
    <>
      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2>项目资料</h2>
          <button className={styles.button} onClick={() => setDialog("files")}>
            添加与管理资料
          </button>
        </div>
        <p className={styles.muted}>
          文件与网页供当前项目任务使用，成果需主动加入资料后才会成为后续任务的输入。
        </p>
        {resources.documents.length + resources.links.length ? (
          <ul className={styles.list}>
            {resources.documents.map((document) => (
              <li className={styles.row} key={document.inputArtifactId}>
                <span className={styles.copy}>
                  <strong>{document.name}</strong>
                  <small>项目输入 · {size(document.sizeBytes)}</small>
                </span>
                <a
                  className={styles.textLink}
                  href={`/api/input-artifacts/${encodeURIComponent(document.inputArtifactId)}/content?inline=1`}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看 ↗
                </a>
              </li>
            ))}
            {resources.links.map((link) => (
              <li className={styles.row} key={link.linkId}>
                <span className={styles.copy}>
                  <strong>{link.title || link.url}</strong>
                  <small>{link.url}</small>
                </span>
                <a
                  className={styles.textLink}
                  href={link.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  打开 ↗
                </a>
              </li>
            ))}
          </ul>
        ) : (
          projectEmpty(
            "先准备这个项目的资料",
            "上传文件或添加网页，任务创建前也能独立整理。",
          )
        )}
      </section>
      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2>项目知识库</h2>
          <button
            className={styles.button}
            onClick={() => setDialog("knowledge")}
          >
            配置知识库
          </button>
        </div>
        <p className={styles.muted}>
          项目知识补充智能体的专业知识，保留各自的授权与来源。
        </p>
        {project.knowledgeReferences?.length ? (
          <ul className={styles.list}>
            {project.knowledgeReferences.map((ref) => (
              <li className={styles.row} key={ref}>
                <span className={styles.copy}>
                  <strong>{knowledgeNames.get(ref) ?? ref}</strong>
                  <small>项目引用 · {ref}</small>
                </span>
                <Link
                  className={styles.textLink}
                  href={`/studio/knowledge/${encodeURIComponent(ref)}`}
                >
                  打开知识库 →
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          projectEmpty(
            "还没有绑定项目知识库",
            "选择本项目需要的资料集合，智能体本身的知识绑定继续保留。",
          )
        )}
      </section>
      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2>项目附加技能</h2>
          <button className={styles.button} onClick={() => setDialog("skills")}>
            选择附加技能
          </button>
        </div>
        <p className={styles.muted}>
          这里只引用额外的技能包。智能体自带技能随发布版本管理；同名时使用智能体自带版本。
        </p>
        {project.skillReferences?.length ? (
          <ul className={styles.list}>
            {project.skillReferences.map((ref) => (
              <li className={styles.row} key={ref}>
                <span className={styles.copy}>
                  <strong>{skillNames.get(ref) ?? ref}</strong>
                  <small>{ref}</small>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          projectEmpty(
            "尚无附加技能",
            "项目任务会继续使用执行智能体自带的技能。",
          )
        )}
      </section>
      <ProjectFacts space={space} />
    </>
  );
}

export function ProjectFacts({ space }: { space: ProjectSpaceState }) {
  const {
    resources,
    memory,
    setMemory,
    editingMemory,
    setEditingMemory,
    memoryPending,
    saveMemory,
    removeMemory,
  } = space;
  if (!resources) return null;
  return (
    <section className={`${styles.section} ${styles.facts}`}>
      <div className={styles.sectionHead}>
        <h2>项目事实与决定</h2>
      </div>
      <p className={styles.muted}>
        这些内容只属于当前项目，不会修改智能体定义。修改后的内容在后续任务执行时生效。
      </p>
      <ul className={styles.list}>
        {resources.memories.map((item) => (
          <li className={styles.row} key={item.memoryId}>
            <span className={styles.copy}>
              <span className={styles.factText}>{item.content}</span>
            </span>
            <button
              className={styles.textLink}
              disabled={memoryPending}
              onClick={() => {
                setEditingMemory(item.memoryId);
                setMemory(item.content);
              }}
            >
              编辑
            </button>
            <button
              className={styles.textLink}
              disabled={memoryPending}
              onClick={() => void removeMemory(item.memoryId)}
            >
              移除
            </button>
          </li>
        ))}
      </ul>
      <form onSubmit={(event) => void saveMemory(event)}>
        <label className={styles.field}>
          {editingMemory ? "编辑项目事实" : "添加项目事实"}
          <textarea
            className={styles.textarea}
            value={memory}
            maxLength={4000}
            placeholder="记录这个项目已确认的业务事实或决定…"
            onChange={(event) => setMemory(event.target.value)}
          />
        </label>
        <div className={styles.actions}>
          {editingMemory && (
            <button
              type="button"
              className={styles.button}
              onClick={() => {
                setEditingMemory(null);
                setMemory("");
              }}
            >
              取消编辑
            </button>
          )}
          <button
            className={styles.button}
            disabled={memoryPending || !memory.trim()}
          >
            {memoryPending ? "保存中…" : "保存项目事实"}
          </button>
        </div>
      </form>
    </section>
  );
}
