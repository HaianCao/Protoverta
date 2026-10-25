"""Run a local Protoverta gateway."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace

from .capabilities import CapabilityRegistry
from .core import Protocol
from .observability import jsonl_observer
from .provider_profiles import ProviderProfiles
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
    parser.add_argument("--diagnostics", action="store_true", help="Opt-in payload-free request outcome JSON lines on stderr; no log file is created")
    parser.add_argument("--timeout", type=float, default=120, help="Socket I/O timeout in seconds; not a total generation deadline")
    parser.add_argument("--max-concurrent-requests", type=int, default=16, help="Maximum accepted handler connections, including unauthenticated clients")
    parser.add_argument("--total-timeout", type=float, default=600)
    parser.add_argument("--progress-timeout", type=float, default=120)
    parser.add_argument("--max-stream-bytes", type=int, default=32 * 1024 * 1024)
    parser.add_argument("--max-stream-items", type=int, default=1024)
    parser.add_argument("--max-tool-argument-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--namespace-tools", choices=["reject", "flatten"], default="reject", help="Opt-in request-scoped aliases for Responses function namespaces")
    parser.add_argument("--client-tool-search", choices=["reject", "function"], default="reject", help="Opt-in JSON/SSE client search via a function; no hosted search")
    parser.add_argument("--tool-names", choices=["reject", "alias"], help="Reversible function aliases; defaults to reject in strict profile, alias in codex/claude profiles; native routes unchanged")
    parser.add_argument("--messages-system-model", action="append", default=[], help="Exact upstream model ID verified to support mid-conversation system messages; repeatable")
    parser.add_argument("--capabilities-file", help="JSON registry of recorded capabilities for exact API prefix/protocol/model IDs")
    parser.add_argument("--provider-profiles-file", help="Evidence-scoped provider wire options; no semantic downgrades")
    parser.add_argument("--shared-admission-db", help="Opt-in dedicated SQLite budget shared by gateway processes")
    parser.add_argument("--shared-requests", type=int, default=40)
    parser.add_argument("--shared-window", type=float, default=60)
    parser.add_argument("--shared-concurrency", type=int, default=5)
    parser.add_argument("--retry-attempts", type=int, default=1, help="1 disables retries; at most 4 explicit HTTP-rejection attempts")
    parser.add_argument("--retry-max-wait", type=float, default=5)
    parser.add_argument("--require-verified-capabilities", action="store_true", help="Reject requested features missing from the configured registry as well as unsupported features")
    parser.add_argument("--require-verified-routes", action="store_true", help="Require exact schema-3 route/policy evidence, separately from upstream model support")
    parser.add_argument("--context-preflight", choices=["off", "estimate", "reject_estimate"], default="off", help="Opt-in labelled heuristic; never an exact tokenizer or automatic history truncation")
    parser.add_argument("--public-reasoning", action="store_true", help="Opt in to unsigned reasoning_content <-> Responses reasoning_text; no summaries, signatures or encrypted state")
    parser.add_argument("--token-count-mode", choices=["off", "estimate", "native"], default="off")
    parser.add_argument("--prompt-caching", choices=["off", "ephemeral"], default="off")
    parser.add_argument("--require-current-evidence", action="store_true")
    parser.add_argument("--evidence-max-age-days", type=int)
    parser.add_argument("--openrouter-provider", help="Opt-in single Chat provider/endpoint slug, no fallback, random per-gateway session affinity")
    args = parser.parse_args()
    key = os.environ.get(args.upstream_key_env, "")
    token = os.environ.get(args.inbound_token_env, "")
    if not key or not token:
        parser.error("both configured environment variables must contain nonempty secrets")
    try:
        capabilities = CapabilityRegistry.load(args.capabilities_file) if args.capabilities_file else None
        profiles = ProviderProfiles.load(args.provider_profiles_file) if args.provider_profiles_file else None
        config = GatewayConfig(Protocol(args.upstream_protocol), args.upstream_base_url, key, token, args.auth_style, client_profile=args.client_profile, default_max_output_tokens=args.default_max_output_tokens, messages_usage_policy=args.messages_usage_policy, upstream_reasoning=args.upstream_reasoning, namespace_tools=args.namespace_tools, messages_system_models=tuple(args.messages_system_model), capabilities=capabilities, require_verified_capabilities=args.require_verified_capabilities, public_reasoning=args.public_reasoning, tool_names=args.tool_names, timeout=args.timeout, max_concurrent_requests=args.max_concurrent_requests)
        config = replace(config, total_timeout=args.total_timeout, progress_timeout=args.progress_timeout, max_stream_bytes=args.max_stream_bytes, max_stream_items=args.max_stream_items, max_tool_argument_bytes=args.max_tool_argument_bytes, client_tool_search=args.client_tool_search, provider_profiles=profiles)
        config = replace(config, shared_admission_db=args.shared_admission_db, shared_requests=args.shared_requests, shared_window=args.shared_window, shared_concurrency=args.shared_concurrency, retry_attempts=args.retry_attempts, retry_max_wait=args.retry_max_wait)
        config = replace(config, require_verified_routes=args.require_verified_routes, context_preflight=args.context_preflight)
        config = replace(config, token_count_mode=args.token_count_mode, prompt_caching=args.prompt_caching, require_current_evidence=args.require_current_evidence, evidence_max_age_days=args.evidence_max_age_days)
        config = replace(config, openrouter_provider=args.openrouter_provider)
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))
    server = serve(config, port=args.port, observer=jsonl_observer(sys.stderr) if args.diagnostics else None)
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
