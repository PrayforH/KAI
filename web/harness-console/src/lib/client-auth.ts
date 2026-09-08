import { publishAuthEvent } from "./auth-coordination";

const SESSION_EXPIRED_PATH = "/login?error=session_expired";
const SESSION_REPLACED_PATH = "/login?error=session_replaced";

type RedirectLocation = Pick<Location, "replace">;

export function redirectOnUnauthorized(
  response: Response,
  location: RedirectLocation | undefined =
    typeof window === "undefined" ? undefined : window.location,
): boolean {
  if (response.status !== 401) return false;
  const replaced = response.headers.get("x-harness-auth-error") === "session_replaced";
  if (typeof window !== "undefined") {
    publishAuthEvent({
      type: "invalidated",
      reason: replaced ? "session_replaced" : "session_expired",
    });
  } else {
    location?.replace(replaced ? SESSION_REPLACED_PATH : SESSION_EXPIRED_PATH);
  }
  return true;
}

export function requireAuthenticatedResponse(response: Response): Response {
  if (redirectOnUnauthorized(response)) {
    throw new Error("登录会话已停止，请按提示重新登录。");
  }
  return response;
}
