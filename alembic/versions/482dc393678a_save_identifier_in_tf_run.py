"""Save identifier in TF run

Revision ID: 482dc393678a
Revises: 46b37040cb19
Create Date: 2022-06-28 07:39:14.713495

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "482dc393678a"
down_revision = "46b37040cb19"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "tft_test_run_targets", sa.Column("identifier", sa.String(), nullable=True)
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("tft_test_run_targets", "identifier")
    # ### end Alembic commands ###
