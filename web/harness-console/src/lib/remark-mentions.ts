interface MentionNode {
  type: string;
  value?: string;
  children?: MentionNode[];
  data?: { hName?: string; hProperties?: Record<string, unknown> };
}

/** 匹配正文里的 @某人：前面是行首/空白/括号才认，邮箱与 URL 里的 @ 不算。 */
const MENTION = /(?<![\w@/])@([\p{L}\p{N}_\-·]{1,32})/gu;

/**
 * 把纯文本里的 @某人 变成带样式的行内节点，供 react-markdown 直接渲染。
 * 代码块与行内代码不参与，避免把代码里的 @ 也画成芯片。
 */
export function remarkMentions() {
  return (tree: MentionNode) => {
    function visit(parent: MentionNode) {
      if (!parent.children) return;
      const next: MentionNode[] = [];
      for (const node of parent.children) {
        if (node.type === "code" || node.type === "inlineCode" || node.type === "html") {
          next.push(node);
          continue;
        }
        if (node.type === "text" && node.value) {
          next.push(...splitText(node.value));
          continue;
        }
        visit(node);
        next.push(node);
      }
      parent.children = next;
    }
    visit(tree);
  };
}

function splitText(value: string): MentionNode[] {
  const parts: MentionNode[] = [];
  let cursor = 0;
  MENTION.lastIndex = 0;
  for (let match = MENTION.exec(value); match; match = MENTION.exec(value)) {
    const start = match.index;
    if (start > cursor) parts.push({ type: "text", value: value.slice(cursor, start) });
    parts.push({
      type: "mention",
      children: [{ type: "text", value: `@${match[1]}` }],
      data: {
        hName: "span",
        hProperties: { className: "group-mention", "data-mention": match[1] },
      },
    });
    cursor = start + match[0].length;
  }
  if (cursor < value.length) parts.push({ type: "text", value: value.slice(cursor) });
  return parts;
}
