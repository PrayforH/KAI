/** Keep historical platform labels consistent without changing agent identity. */
export function agentDisplayName(name: string, label: string = name): string {
  if (name === "lead-agent" && /^(?:lead-agent|通用[\s-]*lead[\s-]*agent)$/i.test(label.trim())) {
    return "通用助手";
  }
  return label;
}
