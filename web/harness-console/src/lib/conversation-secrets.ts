export const SECRET_MASK = "••••••••";
const SECRET_NAME = /密码|口令|密钥|令牌|敏感信息|(?:^|[^a-z])(?:password|passwd|pwd|secret|token|api[_ -]?key)(?:$|[^a-z])/i;
export function isSensitiveLabel(label: string) { return SECRET_NAME.test(label); }
const PLACEHOLDER = /^(?:[•*●]+|已跳过|已隐藏|未填写|未提供|\[REDACTED\]|<redacted>)$/i;
const LABEL_LINE = /^(?:(?:数据库|连接|访问|登录|只读账号|账户|账号|用户|database|connection|login)\s*)?(?:password|passwd|pwd|api[_ -]?key|access[_ -]?token|secret|token|密码|口令|密钥|令牌|敏感信息)(?:\s*[（(][^）)]{0,80}[）)])?\s*[:：]?$/i;
export function isSecretFieldLabel(label: string) { return LABEL_LINE.test(label.trim()) || /[（(]敏感信息[）)]\s*$/.test(label); }
const INLINE_NAME = "(?:password|passwd|pwd|api[_-]?key|access[_-]?token|secret|token|密码|口令|密钥|令牌)";

/** Display projection only. Never mutate the message sent to the runtime. */
function projectSecrets(text: string, found?: Set<string>): string {
  const hide = (secret: string) => {
    if (!secret || PLACEHOLDER.test(secret.trim())) return secret;
    found?.add(secret);
    if (secret.trim()) found?.add(secret.trim());
    return SECRET_MASK;
  };
  let result = text.replace(/([a-z][a-z0-9+.-]*:\/\/[^\s/@:]+:)([^\s/@]+)(@)/gi,
    (_match, prefix, secret, suffix) => prefix + hide(secret) + suffix);
  // JSON, assignment, CLI and ordinary labelled credential values.
  result = result.replace(new RegExp(`(${INLINE_NAME}["']?\\s*[:=：]\\s*)(["'])([^"'\\n]+)\\2`, "gi"),
    (_match, prefix, quote, secret) => prefix + quote + hide(secret) + quote);
  result = result.replace(new RegExp(`((?<![\\w])${INLINE_NAME}["']?\\s*[:=：]\\s*)([^\\s"'\u0060,，;；}\\]]+)`, "gi"),
    (_match, prefix, secret) => prefix + hide(secret));
  let expectSecret = false, expectAccount = false;
  const lines = result.split(/\r?\n/).map(line => {
    const label = line.trim().replace(/^\*\*|\*\*$/g, "");
    if (!label) return line;
    if (isSecretFieldLabel(label)) { expectSecret = true; expectAccount = false; return line; }
    if (expectSecret) {
      expectSecret = false;
      // Missing/skipped values and the next labelled field remain readable.
      if (!PLACEHOLDER.test(label) && !/^(?:主机|端口|用户名|账号|schema|host|port|username)\s*[:：]?/i.test(label)) return hide(line);
    }
    const account = expectAccount;
    expectAccount = /^(?:数据库)?(?:用户名|账号|账户|username|user)(?:\s*[（(][^）)]*[）)])?\s*[:：]?$/i.test(label);
    // Legacy answers sometimes place user/password in the username field, or
    // alongside a DB host. Restrict this to credential context, never paths.
    if (account || /密码|用户名|账号|凭据|password|username|credential|达梦|数据库/i.test(line)) {
      return line.replace(/(?<![\w/])([A-Za-z_][\w.-]{0,63})\/([^\s]+)/g,
        (match, user, secret) => account || /密码|用户名|账号|凭据|password|username|credential/i.test(line) || (/^(?:[A-Z][A-Z0-9_]*|reader|readonly|admin|root|sa)$/.test(user) && /[0-9@!#$%&*+=?:]/.test(secret))
          ? user + "/" + hide(secret) : match);
    }
    return line;
  });
  return lines.join("\n");
}

export function collectConversationSecrets(texts: readonly string[]): string[] {
  const found = new Set<string>();
  for (const text of texts) projectSecrets(text, found);
  return [...found].sort((a,b) => b.length - a.length);
}
export function redactConversationText(text: string, secrets: readonly string[] = []): string {
  let result = projectSecrets(text);
  for (const secret of secrets) {
    if (!secret || PLACEHOLDER.test(secret.trim())) continue;
    if (secret.length >= 4) result = result.split(secret).join(SECRET_MASK);
    else {
      const escaped = secret.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      result = result.replace(new RegExp(`(?<![\\w])${escaped}(?![\\w])`, "g"), SECRET_MASK);
    }
  }
  return result;
}
