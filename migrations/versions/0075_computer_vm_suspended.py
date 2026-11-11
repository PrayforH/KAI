"""Allow a cloud VM to be halted while keeping its instance and work disks."""

import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

_OLD_STATES = "'registered','starting','idle','running','recovering','failed'"
_NEW_STATES = _OLD_STATES + ",'suspended'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_assistant_computer_state", "assistant_computers", type_="check"
    )
    op.create_check_constraint(
        "ck_assistant_computer_state",
        "assistant_computers",
        f"state IN ({_NEW_STATES})",
    )
    op.drop_constraint(
        "ck_assistant_computer_attached", "assistant_computers", type_="check"
    )
    op.create_check_constraint(
        "ck_assistant_computer_attached",
        "assistant_computers",
        "state NOT IN ('idle','running','suspended') OR provider_ref IS NOT NULL",
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE assistant_computers SET state = 'idle' WHERE state = 'suspended'")
    )
    op.drop_constraint(
        "ck_assistant_computer_attached", "assistant_computers", type_="check"
    )
    op.create_check_constraint(
        "ck_assistant_computer_attached",
        "assistant_computers",
        "state NOT IN ('idle','running') OR provider_ref IS NOT NULL",
    )
    op.drop_constraint(
        "ck_assistant_computer_state", "assistant_computers", type_="check"
    )
    op.create_check_constraint(
        "ck_assistant_computer_state",
        "assistant_computers",
        f"state IN ({_OLD_STATES})",
    )
