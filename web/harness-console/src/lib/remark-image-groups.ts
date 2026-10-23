interface ImageNode { type: string; value?: string; children?: ImageNode[]; data?: { hProperties?: Record<string, unknown> }; }
const imageOnly = (node: ImageNode): boolean => node.type === "image"
  || (node.type === "text" && !node.value?.trim())
  || (node.type === "link" && Boolean(node.children?.length) && node.children!.every(imageOnly));
const imageParagraph = (node: ImageNode) => node.type === "paragraph" && Boolean(node.children?.length) && node.children!.every(imageOnly);

/** Adjacent Markdown photos form one compact group; captions and prose keep their order. */
export function remarkImageGroups() {
  return (tree: ImageNode) => {
    function visit(parent: ImageNode) {
      if (!parent.children) return;
      const grouped: ImageNode[] = [];
      for (const node of parent.children) {
        const previous = grouped.at(-1);
        if (imageParagraph(node) && previous && imageParagraph(previous)) previous.children!.push(...node.children!);
        else grouped.push(node);
      }
      parent.children = grouped;
      for (const node of grouped) {
        if (imageParagraph(node) && node.children!.filter(child => child.type !== "text").length > 1) {
          node.data = { ...node.data, hProperties: { ...node.data?.hProperties, className: "conversation-image-group" } };
        } else visit(node);
      }
    }
    visit(tree);
  };
}
