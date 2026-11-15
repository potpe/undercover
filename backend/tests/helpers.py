from sqlalchemy import select, update

from backend.database import claims_table, connect


def seed_working_calculation(claim_id: str, payout: float) -> None:
    """Seed a deterministic approval calculation for tests that bypass payout API."""
    with connect() as connection:
        failed_attempts = connection.execute(
            select(claims_table.c.failed_attempts).where(
                claims_table.c.claim_id == claim_id
            )
        ).scalar_one_or_none()
        if failed_attempts is None:
            return
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(
                current_calculation_attempt=failed_attempts + 1,
                current_calculation={
                    "lines": [{"step": "Test payout", "amount": payout}],
                    "payout": payout,
                },
            )
        )
