"""User-facing failure copy shared by live errors and replayed activity."""

from collections.abc import Mapping


def failure_message(event_type: str, payload: Mapping[str, object]) -> str:
    code = payload.get("error_code")
    error_type = payload.get("error_type")
    if code == "runtime_message_too_large":
        return "批量图片处理产生的运行消息超过容量限制，请继续当前任务并缩小每批处理范围。"
    if code == "runtime_invalid_json" or error_type == "CLIJSONDecodeError":
        return "运行消息过大或格式异常，解析失败。请重试当前任务；批量图片可分批处理。"
    if payload.get("subtype") == "error_max_turns":
        return "该轮触发了历史回合数上限，平台现已取消这项限制，请继续当前任务。"
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message
    if event_type == "run.timed_out":
        return "任务执行超时，请继续当前任务，或缩小本轮处理范围。"
    if event_type == "run.rejected":
        return "当前请求未能执行，请检查运行权限或配置后重试。"
    if error_type in {"CredentialLeaseError", "McpCredentialError"}:
        return "模型或工具连接不可用，请检查能力连接后重试。"
    return "任务运行失败，请重试当前任务；如仍失败，可打开运行详情查看原因。"
