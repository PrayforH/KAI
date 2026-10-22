"""Persistent assistant objectives and execution intents.

Revision ID: 0044
Revises: 0043
"""

from typing import cast

from alembic import op
from sqlalchemy import Table

from harness.assistant.storage import (
    AssistantInboxRow,
    ObjectiveEventRow,
    ObjectiveExecutionRow,
    ObjectiveRow,
)

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for model in (ObjectiveRow, ObjectiveExecutionRow, ObjectiveEventRow, AssistantInboxRow):
        cast(Table, model.__table__).create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for model in (AssistantInboxRow, ObjectiveEventRow, ObjectiveExecutionRow, ObjectiveRow):
        cast(Table, model.__table__).drop(op.get_bind(), checkfirst=True)
