"""Run a local Protoverta gateway."""

from __future__ import annotations

import argparse
import os

from .capabilities import CapabilityRegistry
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
    parser.add_argument("--upstream-reasoning", choices=["provider_default", "disabled", "openrouter_disabled"], default="provider_default", help="Explicit upstream override; disabled replaces request effort, not output filtering")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--namespace-tools", choices=["reject", "flatten"], default="reject", help="Opt-in request-scoped aliases for Responses function namespaces")
    parser.add_argument("--messages-system-model", action="append", default=[], help="Exact upstream model ID verified to support mid-conversation system messages; repeatable")
    parser.add_argument("--capabilities-file", help="JSON registry of recorded capabilities for exact API prefix/protocol/model IDs")
    parser.add_argument("--require-verified-capabilities", action="store_true", help="Reject requested features missing from the configured registry as well as unsupported features")
    args = parser.parse_args()
    key = os.environ.get(args.upstream_key_env, "")
    token = os.environ.get(args.inbound_token_env, "")
    if not key or not token:
        parser.error("both configured environment variables must contain nonempty secrets")
    try:
        capabilities = CapabilityRegistry.load(args.capabilities_file) if args.capabilities_file else None
        config = GatewayConfig(Protocol(args.upstream_protocol), args.upstream_base_url, key, token, args.auth_style, client_profile=args.client_profile, default_max_output_tokens=args.default_max_output_tokens, messages_usage_policy=args.messages_usage_policy, upstream_reasoning=args.upstream_reasoning, namespace_tools=args.namespace_tools, messages_system_models=tuple(args.messages_system_model), capabilities=capabilities, require_verified_capabilities=args.require_verified_capabilities)
    except (ValueError, TypeError, OSError) as exc:
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
