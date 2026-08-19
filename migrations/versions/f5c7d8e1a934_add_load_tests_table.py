"""add load_tests table

Revision ID: f5c7d8e1a934
Revises: e3b9d7a2f651
Create Date: 2026-08-19 14:30:00.000000

Adds the load_tests table for storing load test configuration and results.
Each load_tests row is linked 1:1 to a scans row (job_type='loadtest'),
reusing the scan lifecycle infrastructure. Additive — no existing tables or
columns are modified, so this is fully backward-compatible.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f5c7d8e1a934'
down_revision: Union[str, None] = 'e3b9d7a2f651'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'load_tests',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('scan_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('scans.id'), nullable=False, unique=True),
        # Config
        sa.Column('target_urls', postgresql.JSONB(), nullable=False),
        sa.Column('scenario', sa.String(16), nullable=False, server_default='ramp'),
        sa.Column('virtual_users', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('duration_seconds', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('ramp_stages', postgresql.JSONB(), nullable=True),
        sa.Column('http_method', sa.String(8), nullable=False, server_default='GET'),
        sa.Column('headers_config', postgresql.JSONB(), nullable=True),
        sa.Column('request_body', sa.Text(), nullable=True),
        sa.Column('thresholds', postgresql.JSONB(), nullable=True),
        # Results
        sa.Column('k6_summary', postgresql.JSONB(), nullable=True),
        sa.Column('metrics', postgresql.JSONB(), nullable=True),
        sa.Column('ai_analysis', postgresql.JSONB(), nullable=True),
        sa.Column('timeseries', postgresql.JSONB(), nullable=True),
        sa.Column('breaking_point_vus', sa.Integer(), nullable=True),
        sa.Column('thresholds_passed', sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('load_tests')
