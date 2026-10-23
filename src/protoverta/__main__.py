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
    parser.add_argument("--client-profile", choices=["strict", "codex", "claude"], default="strict")
    parser.add_argument("--messages-usage-policy", choices=["strict", "deferred"], default="strict")
    parser.add_argument("--default-max-output-tokens", type=int)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    key = os.environ.get(args.upstream_key_env, "")
    token = os.environ.get(args.inbound_token_env, "")
    if not key or not token:
        parser.error("both configured environment variables must contain nonempty secrets")
    try:
        config = GatewayConfig(Protocol(args.upstream_protocol), args.upstream_base_url, key, token, args.auth_style, client_profile=args.client_profile, default_max_output_tokens=args.default_max_output_tokens, messages_usage_policy=args.messages_usage_policy)
    except ValueError as exc:
        parser.error(str(exc))
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
