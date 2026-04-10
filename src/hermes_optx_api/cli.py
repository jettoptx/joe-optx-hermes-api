"""CLI entrypoint for hermes-optx-api."""

import argparse
import uvicorn

from hermes_optx_api.config import settings


def main():
    parser = argparse.ArgumentParser(
        description="hermes-optx-api — Enhanced API bridge for Hermes Agent + Workspace"
    )
    parser.add_argument(
        "--host", default=settings.host, help=f"Bind address (default: {settings.host})"
    )
    parser.add_argument(
        "--port", type=int, default=settings.port, help=f"Port (default: {settings.port})"
    )
    parser.add_argument(
        "--hermes-url",
        default=settings.hermes_agent_url,
        help=f"Hermes Agent gateway URL (default: {settings.hermes_agent_url})",
    )
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    args = parser.parse_args()

    # Override settings from CLI args
    if args.hermes_url != settings.hermes_agent_url:
        import os
        os.environ["HERMES_AGENT_URL"] = args.hermes_url

    print(f"hermes-optx-api v0.3.0")
    print(f"  Hermes Agent: {args.hermes_url}")
    print(f"  Listening:    {args.host}:{args.port}")
    print(f"  Memory:       {settings.memory_backend}")
    print()

    uvicorn.run(
        "hermes_optx_api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="debug" if args.debug else "info",
    )


if __name__ == "__main__":
    main()
