"""Trusted identity and run metadata scoped to one claim graph."""

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ClaimContext:
    claim_id: str
    policy_id: str
    customer_id: str
    configuration_version: int
    run_kind: str
    context_url: str | None = None
    memory_id: str | None = None

    @classmethod
    def from_bundle(
        cls,
        claim_id: str,
        bundle: dict[str, Any],
        configuration_version: int,
        run_kind: str,
    ) -> "ClaimContext":
        claim = bundle["claim"]
        return cls(
            claim_id=claim_id,
            policy_id=claim["policy_id"],
            customer_id=claim["customer_id"],
            configuration_version=configuration_version,
            run_kind=run_kind,
            context_url=os.getenv("UNDERCOVER_CONTEXT"),
            memory_id=os.getenv("UNDERCOVER_MEMORY_ID"),
        )

    @property
    def decision_maker(self) -> str:
        return f"UnderCover Agent (configuration version {self.configuration_version})"
