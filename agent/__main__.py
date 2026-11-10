"""Start the UnderCover claim agent for one named claim."""

import argparse
import uuid

from dotenv import load_dotenv

from agent.main import backend


def main() -> None:
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Run one UnderCover claim")
    parser.add_argument("claim_id")
    args = parser.parse_args()
    thread_id = f"cli-{uuid.uuid4()}"
    with backend() as client:
        response = client.post(
            f"/api/claims/{args.claim_id}/run", json={"thread_id": thread_id}
        )
    response.raise_for_status()
    print(f"Claim {args.claim_id} started.")


if __name__ == "__main__":
    main()
