"""群资料库的纯规则：下载地址、名称归一化与大小上限。

存储与鉴权分别复用既有输入附件与知识库子系统，这里只放不依赖它们的部分，
让仓储、服务与 API 层共用同一套口径。
"""

from __future__ import annotations

import re
import unicodedata

from harness.core.errors import ConflictError

GROUP_DESCRIPTION_LIMIT = 500
GROUP_DOCUMENT_NAME_LIMIT = 255
GROUP_ATTACHMENT_LIMIT = 10
GROUP_LIBRARY_DOCUMENT_CONTENT_PATH = (
    "/v1/studio/assistant/groups/{group_id}/library/documents/{document_id}/content"
)


def group_document_download_url(group_id: str, document_id: str) -> str:
    """下载走本群资料库接口，不暴露对象存储的键。"""
    return GROUP_LIBRARY_DOCUMENT_CONTENT_PATH.format(
        group_id=group_id, document_id=document_id
    )


def group_document_name(value: str) -> str:
    """文件名只做展示与去控制字符；保留扩展名，误删会让下载类型失真。"""
    text = unicodedata.normalize("NFKC", value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        raise ConflictError("文件名为空")
    if len(text) > GROUP_DOCUMENT_NAME_LIMIT:
        text = text[:GROUP_DOCUMENT_NAME_LIMIT]
    if any(unicodedata.category(char).startswith("C") for char in text):
        raise ConflictError("文件名不能包含换行或控制字符")
    return text


def group_description(value: str | None) -> str | None:
    """描述可按空串清除；超长直接拒绝，不静默截断群资料。"""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return None
    if len(text) > GROUP_DESCRIPTION_LIMIT:
        raise ConflictError(f"群描述不能超过 {GROUP_DESCRIPTION_LIMIT} 个字")
    if any(unicodedata.category(char).startswith("C") for char in text):
        raise ConflictError("群描述不能包含换行或控制字符")
    return text
