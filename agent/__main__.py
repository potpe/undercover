"""Start the UnderCover claim agent for one named claim."""

import argparse
import uuid

from dotenv import load_dotenv

from agent.main import backend, run_with_context


def main() -> None:
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Run one UnderCover claim")
    parser.add_argument("claim_id")
    args = parser.parse_args()
    thread_id = f"cli-{uuid.uuid4()}"
    with backend() as client:
        response = client.post(
            f"/api/internal/claims/{args.claim_id}/work",
            json={"thread_id": thread_id},
        )
    response.raise_for_status()
    print(run_with_context(args.claim_id, thread_id))


if __name__ == "__main__":
    main()
