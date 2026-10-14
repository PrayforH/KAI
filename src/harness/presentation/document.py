"""Parse final generated HTML scripts without executing model-authored code."""

from __future__ import annotations

import asyncio
import json
import shutil
from html.parser import HTMLParser

from harness.presentation.schema import MAX_HTML_BYTES

_JAVASCRIPT_TYPES = {"", "module", "text/javascript", "application/javascript"}
_CHECK = """
const fs = require('node:fs'), vm = require('node:vm');
const scripts = JSON.parse(fs.readFileSync(0, 'utf8'));
for (let index = 0; index < scripts.length; index++) {
  try {
    const script = scripts[index];
    if (script.module) new vm.SourceTextModule(script.source);
    else new vm.Script(script.source);
  } catch (error) {
    process.stdout.write(JSON.stringify({script: index + 1, error: error.message}));
    process.exit(1);
  }
}
"""


class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.scripts: list[dict[str, str | bool]] = []
        self._current: dict[str, str | bool] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attributes = dict(attrs)
        kind = (attributes.get("type") or "").lower().strip()
        if kind not in _JAVASCRIPT_TYPES:
            return
        if attributes.get("src"):
            raise ValueError("交互 HTML 必须自包含，请将外部脚本内联后重新发布")
        self._current = {"source": "", "module": kind == "module"}

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["source"] = str(self._current["source"]) + data

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append(self._current)
            self._current = None

    def close(self) -> None:
        super().close()
        if self._current is not None:
            raise ValueError("HTML 的 script 标签未闭合，请修复最终文件后重新发布")


async def validate_html_document(content: bytes) -> None:
    # Larger files cannot be an inline tool; ordinary downloadable artifacts
    # retain their existing behavior.
    if len(content) > MAX_HTML_BYTES:
        return
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("交互 HTML 必须使用 UTF-8") from error
    parser = _Scripts()
    parser.feed(text)
    parser.close()
    if not parser.scripts:
        return
    node = shutil.which("node")
    if node is None:
        raise ValueError("HTML 脚本检查需要运行环境提供 Node.js")
    process = await asyncio.create_subprocess_exec(
        node,
        "--experimental-vm-modules",
        "-e",
        _CHECK,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(3):
            output, _ = await process.communicate(json.dumps(parser.scripts).encode())
    except TimeoutError as error:
        raise ValueError("最终 HTML 的脚本检查超时，请简化脚本后重试") from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode:
        detail = output.decode("utf-8", errors="replace")[:400]
        raise ValueError(f"最终 HTML 的 JavaScript 语法检查失败：{detail}。修复后重新发布")
