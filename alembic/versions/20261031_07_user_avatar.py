"""Add optional avatar to users."""
from alembic import op
import sqlalchemy as sa

revision = '20261031_07'
down_revision = '20261030_06'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c['name'] for c in inspector.get_columns('users')}
    if 'avatar' not in columns:
        with op.batch_alter_table('users') as batch:
            batch.add_column(sa.Column('avatar', sa.Text(), nullable=True))

    # Repair legacy global unique constraint/index on branches(code) if present,
    # ensuring uniqueness is scoped to (tenant_id, code) so every company can have a 'PRINCIPAL' branch.
    if inspector.has_table('branches'):
        branch_uqs = inspector.get_unique_constraints('branches')
        branch_idxs = inspector.get_indexes('branches')
        has_scoped_uq = any(
            list(uq.get('column_names') or []) == ['tenant_id', 'code'] for uq in branch_uqs
        ) or any(
            idx.get('unique') and list(idx.get('column_names') or []) == ['tenant_id', 'code']
            for idx in branch_idxs
        )
        single_code_uqs = [
            uq['name']
            for uq in branch_uqs
            if uq.get('name') and list(uq.get('column_names') or []) == ['code']
        ]
        single_code_idxs = [
            idx['name']
            for idx in branch_idxs
            if idx.get('name') and idx.get('unique') and list(idx.get('column_names') or []) == ['code']
        ]
        if single_code_uqs or single_code_idxs or not has_scoped_uq:
            for idx_name in single_code_idxs:
                op.drop_index(idx_name, table_name='branches')
            with op.batch_alter_table('branches') as batch:
                for uq_name in single_code_uqs:
                    batch.drop_constraint(uq_name, type_='unique')
                if not has_scoped_uq:
                    batch.create_unique_constraint('uq_branch_company_code', ['tenant_id', 'code'])


def downgrade():
    with op.batch_alter_table('users') as batch:
        batch.drop_column('avatar')

