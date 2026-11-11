# ruff: noqa: E501, E701, E702
"""79 群聊 A–L 回归脚本（P0/P1）。

先在本机登录 79 并把浏览器 Cookie 导出为 Netscape 格式，默认读取
/tmp/kai-cookies.txt；不要把密码写进本文件。可用环境变量覆盖：

  GROUP_CHAT_BASE      默认 https://172.20.105.79:3543/api/studio/assistant
  GROUP_CHAT_COOKIES   默认 /tmp/kai-cookies.txt
  GROUP_CHAT_SPECIALISTS 逗号分隔的两个子助理名，默认 编码协同,天气提醒
"""

import http.cookiejar
import json
import os
import ssl
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get(
    "GROUP_CHAT_BASE", "https://172.20.105.79:3543/api/studio/assistant"
)
COOKIES = os.environ.get("GROUP_CHAT_COOKIES", "/tmp/kai-cookies.txt")
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
jar = http.cookiejar.MozillaCookieJar(COOKIES); jar.load(ignore_discard=True, ignore_expires=True)
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=ctx))

def call(path, method="GET", body=None, ok=(200, 201, 204)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}/{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: payload = json.loads(raw)
        except Exception: payload = raw.decode(errors="replace")
        return e.code, payload

def terminal(m): return m["status"] in ("completed", "failed", "stopped")
def wait(user_msg_id, timeout=240):
    start = time.time(); last = None
    while time.time() - start < timeout:
        _, msgs = call(f"groups/{GROUP}/messages")
        replies = [m for m in msgs if m.get("replyToMessageId") == user_msg_id]
        last = replies
        if replies and all(terminal(m) for m in replies):
            return replies
        time.sleep(4)
    return last or []

def post(text):
    status, result = call(f"groups/{GROUP}/messages", "POST",
                          {"text": text, "clientMessageId": f"verify-{uuid.uuid4().hex}"})
    assert status == 201, (status, result)
    return result

def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + (f" | {detail}" if detail else ""), flush=True)
    if not condition: raise SystemExit(1)

# A. 空名称
status, payload = call("groups", "POST", {"name": "   "})
check("A 空名称明确失败", status in (409, 422) and not (isinstance(payload, dict) and payload.get("groupId")), f"status={status}")

# B. 建群
name = "群聊验收-" + time.strftime("%H%M%S")
status, group = call("groups", "POST", {"name": name})
check("B 建群成功", status == 201 and group["name"] == name, f"status={status}")
GROUP = group["groupId"]
keys = {(m["memberId"], m["kind"], m["name"]) for m in group["members"]}
check("B 创建者+主助理默认在群", any(k[1] == "creator" and k[2] == "创建者" for k in keys)
      and any(k[1] == "primary" and k[2] == "主助理" for k in keys), str(keys))

# C. 拉人
status, specialists = call("specialists")
by_name = {s["name"]: s for s in specialists}
names = os.environ.get("GROUP_CHAT_SPECIALISTS", "编码协同,天气提醒").split(",")
coder = by_name[names[0]]; weather = by_name[names[1]]
for s in (coder, weather):
    status, group = call(f"groups/{GROUP}/members", "POST", {"specialistId": s["specialistId"]})
    check(f"C 邀请 {s['name']}", status == 201 and any(m["specialistId"] == s["specialistId"] for m in group["members"]))

# D. 未 @ → 主助理
r = post("请只回复 OK，不要调用工具。")
check("D 未 @ 立即入时间线", r["message"]["text"].startswith("请只回复"))
replies = wait(r["message"]["messageId"])
check("D 未 @ 只有主助理接话", len(replies) == 1 and replies[0]["author"] == "primary" and replies[0]["text"].strip(),
      json.dumps(replies, ensure_ascii=False)[:300])
plain_replies = replies

# E. @ 子助理
r = post(f"@{names[0]} 请只回复 SPECIALIST，不要调用工具。")
check("E @ 子助理提示占位", len([x for x in r["replies"] if x["author"] == "specialist"]) == 1)
replies = wait(r["message"]["messageId"])
check("E 只有被 @ 的子助理回复", len(replies) == 1 and replies[0]["specialistId"] == coder["specialistId"]
      and replies[0]["text"].strip(), json.dumps(replies, ensure_ascii=False)[:300])

# F. @ 主助理
r = post("@主助理 请只回复 PRIMARY，不要调用工具。")
replies = wait(r["message"]["messageId"])
check("F @ 主助理由主助理直接回", len(replies) == 1 and replies[0]["author"] == "primary"
      and replies[0]["text"].strip(), json.dumps(replies, ensure_ascii=False)[:300])

# G. 多 @
r = post(f"@{names[0]} @{names[1]} 请各自只回复 OK，不要调用工具。")
replies = wait(r["message"]["messageId"], timeout=300)
ids = {m["specialistId"] for m in replies}
check("G 多 @ 被点名者都回", len(replies) == 2 and ids == {coder["specialistId"], weather["specialistId"]},
      json.dumps(replies, ensure_ascii=False)[:300])

# H. 移出天气提醒后再 @
status, group = call(f"groups/{GROUP}/members/{weather['specialistId']}", "DELETE")
check("H 移出成员消失于花名册", not any(m["specialistId"] == weather["specialistId"] for m in group["members"]))
r = post(f"@{names[1]} 还在吗？")
check("H 被移出成员给明确提示", any("不在本群" in n for n in r["notices"]), str(r["notices"]))
check("H 没有唤醒被移出成员", not any(x.get("specialistId") == weather["specialistId"] for x in r["replies"]))
check("H 无效 @ 不改派给任何人", len(r["replies"]) == 1 and r["replies"][0]["author"] == "system"
      and "不在本群" in r["replies"][0]["text"], json.dumps(r["replies"], ensure_ascii=False)[:300])

# I. P1 接话人配置
status, group = call(f"groups/{GROUP}", "PATCH", {"replyAs": coder["specialistId"]})
check("I 接话人配置成功", status == 200 and group["replyAs"] == coder["specialistId"])
r = post("请只回复 RESPONDER，不要调用工具。")
replies = wait(r["message"]["messageId"])
check("I 未 @ 由配置的接话人回复", len(replies) == 1 and replies[0]["specialistId"] == coder["specialistId"],
      json.dumps(replies, ensure_ascii=False)[:300])

# J. 接话人被移出 → 不改派
status, _ = call(f"groups/{GROUP}/members/{coder['specialistId']}", "DELETE")
r = post("继续")
check("J 接话人被移出给明确提示", any("不会自动改派" in n for n in r["notices"]), str(r["notices"]))
check("J 系统消息入时间线", any(x["author"] == "system" for x in r["replies"]))
check("J 没有改派给主助理", not any(x["author"] == "primary" for x in r["replies"]))

# L. 第二个群：移出主助理
status, group2 = call("groups", "POST", {"name": name + "-主助理移出"})
G2 = group2["groupId"]
status, group2 = call(f"groups/{G2}/members/primary", "DELETE")
check("L 主助理移出成功", status == 200 and group2["primaryPresent"] is False)
status, r = call(f"groups/{G2}/messages", "POST", {"text": "还有人吗", "clientMessageId": f"verify-{uuid.uuid4().hex}"})
check("L 未 @ 不再改派", status == 201 and not any(x["author"] in ("primary", "specialist") for x in r["replies"])
      and any("不会改派" in n for n in r["notices"]), json.dumps(r, ensure_ascii=False)[:300])
check("L 主助理移出仍有系统提示", any(x["author"] == "system" for x in r["replies"]))

# K. 隔离：两个群时间线互不串
_, m1 = call(f"groups/{GROUP}/messages")
_, m2 = call(f"groups/{G2}/messages")
ids1 = {m["messageId"] for m in m1}; ids2 = {m["messageId"] for m in m2}
check("K 两个群消息互不泄漏", not (ids1 & ids2) and len(ids1) > 0 and len(ids2) > 0)
check("K 消息只属于本群", all(m["groupId"] == GROUP for m in m1) and all(m["groupId"] == G2 for m in m2))

print(json.dumps({"group": GROUP, "group2": G2, "name": name}, ensure_ascii=False))
