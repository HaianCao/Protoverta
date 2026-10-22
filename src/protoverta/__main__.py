"""Run a local Protoverta gateway."""

from __future__ import annotations

import argparse
import os

from .core import Protocol
from .server import GatewayConfig, serve


def main() -> int:
    parser = argparse.ArgumentParser(description="Protoverta local LLM protocol gateway")
    parser.add_argument("--upstream-protocol", choices=list(Protocol), required=True)
    parser.add_argument("--upstream-base-url", required=True)
    parser.add_argument("--upstream-key-env", required=True)
    parser.add_argument("--inbound-token-env", required=True)
    parser.add_argument("--auth-style", choices=["bearer", "x-api-key"], default="bearer")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    key = os.environ.get(args.upstream_key_env, "")
    token = os.environ.get(args.inbound_token_env, "")
    if not key or not token:
        parser.error("both configured environment variables must contain nonempty secrets")
    config = GatewayConfig(Protocol(args.upstream_protocol), args.upstream_base_url, key, token, args.auth_style)
    server = serve(config, port=args.port)
    print(f"READY http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
