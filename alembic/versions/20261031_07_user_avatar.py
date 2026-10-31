"""Add optional avatar to users."""
from alembic import op
import sqlalchemy as sa

revision = '20261031_07'
down_revision = '20261030_06'
branch_labels = None
depends_on = None


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('users')}
    if 'avatar' not in columns:
        with op.batch_alter_table('users') as batch:
            batch.add_column(sa.Column('avatar', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('users') as batch:
        batch.drop_column('avatar')

