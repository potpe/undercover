"""Start an UnderCover queue or named-claim run from the command line."""

import argparse
import uuid

from dotenv import load_dotenv

from agent.main import backend


def main() -> None:
    load_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Run the UnderCover Agent")
    parser.add_argument("--claim-id")
    parser.add_argument("--claim-cap", type=int, default=5)
    args = parser.parse_args()
    thread_id = f"cli-{uuid.uuid4()}"
    with backend() as client:
        if args.claim_id:
            response = client.post(
                f"/api/claims/{args.claim_id}/run", json={"thread_id": thread_id}
            )
        else:
            response = client.post(
                "/api/runs",
                json={"thread_id": thread_id, "claim_cap": args.claim_cap},
            )
    response.raise_for_status()
    print(f"Run {response.json()['run_id']} started.")


if __name__ == "__main__":
    main()
