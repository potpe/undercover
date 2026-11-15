"""Run the local context MCP service."""

import uvicorn

from context.server import app


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8006)


if __name__ == "__main__":
    main()
