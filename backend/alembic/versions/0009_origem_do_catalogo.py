"""origem do catalogo

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-04 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('organizations', sa.Column('catalog_source', sa.String(length=20), nullable=True))
    # Até esta versão, nenhum ambiente usou o ERP real: todo catálogo existente veio do simulado.
    op.execute("UPDATE organizations SET catalog_source = 'mock' "
               "WHERE id IN (SELECT organization_id FROM product_references)")


def downgrade() -> None:
    with op.batch_alter_table('organizations') as batch:
        batch.drop_column('catalog_source')
