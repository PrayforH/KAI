"""Keep Agent catalog projections compatible with rollback releases.

Revision ID: 0029
Revises: 0028

Release rollback keeps the database at the latest schema revision. Older API
images only write the immutable payload column, so a database trigger derives
the lightweight catalog projection before NOT NULL constraints are checked.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_agent_version_catalog_projection()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.status IS NULL THEN
                NEW.status := NEW.payload::jsonb ->> 'status';
            END IF;
            IF NEW.manifest_hash IS NULL THEN
                NEW.manifest_hash := NEW.payload::jsonb ->> 'manifest_hash';
            END IF;
            IF NEW.package_hash IS NULL THEN
                NEW.package_hash := NEW.payload::jsonb ->> 'package_hash';
            END IF;
            IF NEW.created_at IS NULL THEN
                NEW.created_at := (NEW.payload::jsonb ->> 'created_at')::timestamptz;
            END IF;
            IF NEW.catalog_manifest IS NULL THEN
                NEW.catalog_manifest := COALESCE(
                    NEW.payload::jsonb -> 'snapshot' -> 'manifest',
                    '{}'::jsonb
                )::json;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_versions_catalog_projection
        BEFORE INSERT OR UPDATE OF payload ON agent_versions
        FOR EACH ROW
        EXECUTE FUNCTION sync_agent_version_catalog_projection()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_agent_versions_catalog_projection "
        "ON agent_versions"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS sync_agent_version_catalog_projection()"
    )
