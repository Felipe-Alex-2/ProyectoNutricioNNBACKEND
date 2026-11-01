"""Link consultation charges and add the wallet ledger."""
from alembic import op
import sqlalchemy as sa

revision = '20261031_08'
down_revision = '20261031_07'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c['name'] for c in inspector.get_columns('payments')}
    uniques = {tuple(c['column_names']) for c in inspector.get_unique_constraints('payments')}
    foreign_keys = {tuple(c['constrained_columns']) for c in inspector.get_foreign_keys('payments')}
    indexes = {i['name'] for i in inspector.get_indexes('payments')}
    with op.batch_alter_table('payments') as batch:
        if 'patient_id' not in columns:
            batch.add_column(sa.Column('patient_id', sa.String(36), nullable=True))
        if 'appointment_id' not in columns:
            batch.add_column(sa.Column('appointment_id', sa.String(36), nullable=True))
        if 'purpose' not in columns:
            batch.add_column(sa.Column('purpose', sa.String(30), server_default='POS', nullable=False))
        if ('patient_id',) not in foreign_keys:
            batch.create_foreign_key('fk_payment_patient', 'users', ['patient_id'], ['id'])
        if ('appointment_id',) not in foreign_keys:
            batch.create_foreign_key('fk_payment_appointment', 'appointments', ['appointment_id'], ['id'])
        if ('appointment_id',) not in uniques:
            batch.create_unique_constraint('uq_payment_appointment', ['appointment_id'])
        if 'ix_payments_patient_id' not in indexes:
            batch.create_index('ix_payments_patient_id', ['patient_id'])
    from app.models.wallet import WalletMovement
    WalletMovement.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table('wallet_movements')
    with op.batch_alter_table('payments') as batch:
        batch.drop_index('ix_payments_patient_id')
        batch.drop_constraint('uq_payment_appointment', type_='unique')
        batch.drop_constraint('fk_payment_appointment', type_='foreignkey')
        batch.drop_constraint('fk_payment_patient', type_='foreignkey')
        for name in ('purpose', 'appointment_id', 'patient_id'):
            batch.drop_column(name)
