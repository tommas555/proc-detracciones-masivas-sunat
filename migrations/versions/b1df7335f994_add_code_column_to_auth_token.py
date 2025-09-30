"""Add code column to auth_token

Revision ID: b1df7335f994
Revises: e5f40fb3647d
Create Date: 2025-09-29 21:30:38.450186

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1df7335f994'
down_revision = 'e5f40fb3647d'
branch_labels = None
depends_on = None


# def upgrade():
#     pass


# def downgrade():
#     pass



from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('auth_token', sa.Column('code', sa.String(6), nullable=True))

def downgrade():
    op.drop_column('auth_token', 'code')