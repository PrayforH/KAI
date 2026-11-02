"""助理资料库（Vault）：本人所有、按用途授权、只回脱敏元数据。

值一律进既有加密凭据表；这里只留标题、分类、末四位等可展示的元数据。
卡片只保存安全令牌与末四位——没有 CVV 字段，也不实现付款。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from harness.studio.models import StudioModel


class VaultCategory(StrEnum):
    """全部 / 登录密钥 / 卡片 / 联系信息。"""

    LOGIN = "login"
    CARD = "card"
    CONTACT = "contact"


VAULT_CATEGORY_LABELS: dict[str, str] = {
    VaultCategory.LOGIN.value: "登录密钥",
    VaultCategory.CARD.value: "卡片",
    VaultCategory.CONTACT.value: "联系信息",
}


class VaultItem(StudioModel):
    """列表里出现的全部内容：没有敏感值，只有脱敏元数据。"""

    item_id: str = Field(alias="itemId")
    category: VaultCategory
    title: str
    reference: str
    has_secret: bool = Field(alias="hasSecret")
    last4: str | None = None
    revision: int
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class VaultItemWrite(StudioModel):
    """新建一条资料：分类、标题与可选的值。

    卡片类不接受 CVV——schema 里没有这个字段，传了就是非法输入。
    """

    category: VaultCategory
    title: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, max_length=8_000)
    last4: str | None = Field(default=None, max_length=4, pattern=r"^[0-9A-Za-z]{0,4}$")


class VaultGrantRequest(StudioModel):
    """把一条资料授权给某个 Bot：写 credential_ref，撤销时移除。"""

    specialist_id: str = Field(alias="specialistId", min_length=1, max_length=128)
    granted: bool = True
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class VaultItemUpdate(StudioModel):
    """编辑一条资料：只改标题/末四位时不必重填值。"""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    value: str | None = Field(default=None, max_length=8_000)
    last4: str | None = Field(default=None, max_length=4, pattern=r"^[0-9A-Za-z]{0,4}$")
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class VaultDeleteRequest(StudioModel):
    """删除必须带上标题确认：手滑不该清掉一条资料。"""

    confirm_title: str = Field(alias="confirmTitle", min_length=1, max_length=120)
