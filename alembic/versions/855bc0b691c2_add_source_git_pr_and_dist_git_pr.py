"""Add source git PR and dist git PR

Revision ID: 855bc0b691c2
Revises: 6c9e1df74fa0
Create Date: 2022-03-21 07:47:50.345046

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "855bc0b691c2"
down_revision = "6c9e1df74fa0"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "source_git_pr_dist_git_pr",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_git_pull_request_id", sa.Integer(), nullable=True),
        sa.Column("dist_git_pull_request_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["dist_git_pull_request_id"],
            ["pull_requests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_git_pull_request_id"],
            ["pull_requests.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_source_git_pr_dist_git_pr_dist_git_pull_request_id"),
        "source_git_pr_dist_git_pr",
        ["dist_git_pull_request_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_source_git_pr_dist_git_pr_source_git_pull_request_id"),
        "source_git_pr_dist_git_pr",
        ["source_git_pull_request_id"],
        unique=True,
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(
        op.f("ix_source_git_pr_dist_git_pr_source_git_pull_request_id"),
        table_name="source_git_pr_dist_git_pr",
    )
    op.drop_index(
        op.f("ix_source_git_pr_dist_git_pr_dist_git_pull_request_id"),
        table_name="source_git_pr_dist_git_pr",
    )
    op.drop_table("source_git_pr_dist_git_pr")
    # ### end Alembic commands ###
