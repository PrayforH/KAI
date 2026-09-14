import { defaultUrlTransform } from "react-markdown";
import type { RunActivity } from "./activity-schema";
import { reduceRunViewModel, type RunViewModel } from "./run-view-model";
import type { RunCitation } from "./run-view-model";

export function parseWikiTarget(target: string): { slug: string; reference?: string } {
  const separator = target.indexOf("::");
  return separator > 0
    ? { reference: target.slice(0, separator), slug: target.slice(separator + 2) }
    : { slug: target };
}

export function citationTarget(citation: Pick<RunCitation, "sourceReference" | "chunkId">): string {
  return `citation:${encodeURIComponent(citation.sourceReference)}:${encodeURIComponent(citation.chunkId)}`;
}

export function knowledgeUrlTransform(url: string): string {
  return /^(wiki|citation):/.test(url) ? url : defaultUrlTransform(url);
}

export function dedupeCitations(citations: readonly RunCitation[]): RunCitation[] {
  const seen = new Set<string>();
  return citations.filter((citation) => {
    const key = citationTarget(citation);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).map((citation, index) => ({ ...citation, index: index + 1 }));
}

// Transform only Markdown text nodes, so code examples and existing links stay literal.
type MarkdownNode = { type: string; value?: string; url?: string; children?: MarkdownNode[] };
export function remarkWikiLinks() {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (!node.children || ["link", "code", "inlineCode"].includes(node.type)) return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || !child.value) { visit(child); return [child]; }
        const pattern = /\[\[([^\]|\n]+)(?:\|([^\]\n]+))?\]\]/g;
        const result: MarkdownNode[] = [];
        let cursor = 0;
        for (const match of child.value.matchAll(pattern)) {
          if (match.index! > cursor) result.push({ type: "text", value: child.value.slice(cursor, match.index) });
          result.push({ type: "link", url: `wiki:${encodeURIComponent(match[1].trim())}`, children: [{ type: "text", value: (match[2] ?? match[1]).trim() }] });
          cursor = match.index! + match[0].length;
        }
        if (!cursor) return [child];
        if (cursor < child.value.length) result.push({ type: "text", value: child.value.slice(cursor) });
        return result;
      });
    };
    visit(tree);
  };
}

export function citationsForTurn(messageId: string, isLast: boolean, observed: RunViewModel | null | undefined, durable?: RunActivity): RunCitation[] | undefined {
  const prefix = observed ? `assistant-${observed.runId}` : "";
  const ownsObserved = observed && (messageId === prefix || messageId.startsWith(`${prefix}-`) || (!messageId.startsWith("assistant-") && isLast));
  const view = durable ? reduceRunViewModel(undefined, durable) : ownsObserved ? observed : null;
  return view ? dedupeCitations(view.tools.flatMap((tool) => tool.citations ?? [])) : undefined;
}
