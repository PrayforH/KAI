import type { ConversationQuestion } from "./conversation-input";

export type CredentialRequest = { name: string; label: string; category: "login" | "secret" };

/** Compatibility for older questions without explicit credential metadata. */
export function credentialRequest(name: string, question?: ConversationQuestion): CredentialRequest {
  const login = question?.credentialType === "login" || question?.credentialType !== "secret" &&
    /(?:^|[\W_])login(?:$|[\W_])|登录|(?:用户名|账号|账户).*密码/i.test(`${name} ${question?.label ?? ""}`);
  const readable = question?.label ?? name;
  return { name, category: login ? "login" : "secret",
    label: login ? "登录信息" : /[\u3400-\u9fff]/.test(readable) ? readable : "访问密钥" };
}

/** One encrypted credential value; neither field becomes a chat answer. */
export function loginCredentialValue(username: string, password: string): string {
  return JSON.stringify({ version: 1, category: "login", username: username.trim(), password });
}
