"""add email verification and account fields"""

from alembic import op
import sqlalchemy as sa

revision = 'add_email_verification_fields'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('user', sa.Column('is_email_verified', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('user', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user', sa.Column('first_name', sa.String(120), nullable=True))
    op.add_column('user', sa.Column('last_name', sa.String(120), nullable=True))
    op.add_column('user', sa.Column('account_status', sa.String(24), nullable=False, server_default='TRIAL_ACTIVO'))
    op.add_column('user', sa.Column('payment_proof_submitted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user', sa.Column('payment_proof_url', sa.Text(), nullable=True))
    op.add_column('user', sa.Column('payment_reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user', sa.Column('payment_reviewed_by_admin_id', sa.Integer(), nullable=True))

    op.create_index('ix_user_is_email_verified', 'user', ['is_email_verified'])
    op.create_index('ix_user_account_status', 'user', ['account_status'])

def downgrade():
    op.drop_index('ix_user_account_status', table_name='user')
    op.drop_index('ix_user_is_email_verified', table_name='user')
    
    op.drop_column('user', 'payment_reviewed_by_admin_id')
    op.drop_column('user', 'payment_reviewed_at')
    op.drop_column('user', 'payment_proof_url')
    op.drop_column('user', 'payment_proof_submitted_at')
    op.drop_column('user', 'account_status')
    op.drop_column('user', 'last_name')
    op.drop_column('user', 'first_name')
    op.drop_column('user', 'email_verified_at')
    op.drop_column('user', 'is_email_verified')