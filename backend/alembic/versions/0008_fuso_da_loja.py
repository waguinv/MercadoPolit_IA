"""fuso da loja

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-04 09:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('stores', sa.Column('timezone', sa.String(length=64), nullable=False,
                                      server_default='America/Sao_Paulo'))


def downgrade() -> None:
    with op.batch_alter_table('stores') as batch:
        batch.drop_column('timezone')
