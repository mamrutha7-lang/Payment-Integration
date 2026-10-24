"""fees and razorpay payments

Revision ID: a7d3f1c8e2b9
Revises: daaeb23b750e
Create Date: 2026-10-24 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7d3f1c8e2b9'
down_revision: Union[str, None] = 'daaeb23b750e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('fees',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('student_id', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('amount_paise', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(), nullable=False, server_default='INR'),
    sa.Column('due_date', sa.Date(), nullable=True),
    sa.Column('status', sa.String(), nullable=False, server_default='PENDING'),
    sa.Column('created_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('amount_paise > 0', name='ck_fee_amount_positive'),
    sa.CheckConstraint("status IN ('PENDING','PAID','CANCELLED')", name='ck_fee_status'),
    sa.ForeignKeyConstraint(['student_id'], ['students.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_fees_student_status', 'fees', ['student_id', 'status'], unique=False)
    op.create_table('fee_payments',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('fee_id', sa.String(), nullable=False),
    sa.Column('amount_paise', sa.Integer(), nullable=False),
    sa.Column('razorpay_order_id', sa.String(), nullable=False),
    sa.Column('razorpay_payment_id', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False, server_default='CREATED'),
    sa.Column('failure_reason', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('CREATED','PAID','FAILED')", name='ck_fee_payment_status'),
    sa.ForeignKeyConstraint(['fee_id'], ['fees.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('razorpay_order_id')
    )
    op.create_index(op.f('ix_fee_payments_fee_id'), 'fee_payments', ['fee_id'], unique=False)
    op.create_index(op.f('ix_fee_payments_razorpay_payment_id'), 'fee_payments', ['razorpay_payment_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_fee_payments_razorpay_payment_id'), table_name='fee_payments')
    op.drop_index(op.f('ix_fee_payments_fee_id'), table_name='fee_payments')
    op.drop_table('fee_payments')
    op.drop_index('ix_fees_student_status', table_name='fees')
    op.drop_table('fees')
