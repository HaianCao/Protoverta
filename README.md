<div align="center">

<img src="https://raw.githubusercontent.com/HaianCao/Protoverta/main/assets/protoverta_logo.png" alt="Protoverta logo" width="160">

# Protoverta

Universal LLM Protocol Adapter

Chat Completions <-> Messages <-> Responses

One Gateway. Any Protocol. Any Model.

<p>
  <a href="https://haiancao.github.io/Protoverta/"><img alt="Documentation" src="https://img.shields.io/badge/docs-online-2f6f8f?style=flat-square"></a>
  <a href="https://github.com/HaianCao/Protoverta/blob/main/LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-5a4fcf?style=flat-square"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white">
  <img alt="Windows and Linux" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-0078d4?style=flat-square">
  <img alt="No dependencies" src="https://img.shields.io/badge/dependencies-none-2e7d32?style=flat-square">
  <a href="https://github.com/HaianCao/Protoverta/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/HaianCao/Protoverta/ci.yml?branch=main&style=flat-square&label=tests"></a>
  <a href="https://github.com/HaianCao/Protoverta/releases"><img alt="Latest release" src="https://img.shields.io/github/v/release/HaianCao/Protoverta?style=flat-square&label=release"></a>
</p>

</div>

Protoverta converts requests and responses among OpenAI Chat Completions, Anthropic Messages, and OpenAI Responses. Use it as a Python library or run it as a local HTTP gateway.

**Current scope:** text, user images and inline PDF inputs, client-side function tools, forced function selection, and supported JSON output formats, including incremental SSE where usage and event ordering can be preserved. Optional policies support token counting, certified cache checkpoints and native Responses compaction. Unsupported fields, audio/video conversion and nonportable server-owned state are rejected rather than silently changed.

**Ready-made launchers.** To use Codex or Claude Code with another provider
without configuring the gateway yourself, install one of the launchers built on
Protoverta:

| Launcher | Runs | Command |
| --- | --- | --- |
| [CodexApter](https://github.com/HaianCao/CodexApter) | OpenAI Codex CLI with Chat Completions, Messages or Responses providers | `codex-ca` |
| [ClaudApter](https://github.com/HaianCao/ClaudApter) | Claude Code with Messages, Chat Completions or Responses providers | `claude-ca` |

Tested providers and their limits: [provider compatibility](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/providers.md).

## Install

Python 3.11+ is required; there are no dependencies.

```powershell
python -m pip install protoverta
```

Full documentation — guides, the gateway's HTTP API, the command reference and the Python API — is at **[haiancao.github.io/Protoverta](https://haiancao.github.io/Protoverta/)**. To work on Protoverta itself, see [CONTRIBUTING.md](https://github.com/HaianCao/Protoverta/blob/main/CONTRIBUTING.md).

## Library

```python
from protoverta import convert_request, convert_response

messages_request = {
    "model": "your-model-id",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "Hello"}],
}

chat_request = convert_request(messages_request, "messages", "chat")
# messages_response = convert_response(chat_response, "chat", "messages")
```

`ConversionError` indicates an unsupported or invalid JSON conversion. For SSE, `StreamTranscoder` accepts upstream bytes via `feed()` and requires `finish()` at EOF; it raises `StreamError` on invalid or interrupted streams. Protocol names are `chat`, `messages`, and `responses`.

Bare conversion APIs omit unknown tools/choice/parallel policy. Supply the original
request via `request_context` to preserve known metadata in JSON/SSE. The HTTP
gateway establishes and enforces its effective per-request tool policy; see
[Responses metadata](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/response-metadata.md) for the contract and strict SDK limits.

`top_p` maps across all six request routes with explicit range/type validation;
omitted values do not invent a sampling setting. Provider/model support remains
subject to capability checks.

Image-bearing tool results map between Messages and Responses without changing
their role or call ID. Explicit strict function flags map between Chat and
Responses. Stop-sequence request controls map between Chat and Messages, but
matched-stop response metadata remains nonportable; see
[the exact boundaries](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/output-formats.md).

Completed JSON custom tools map directly between Chat and Responses, preserving
raw input and text/grammar formats. Cross-protocol custom streaming and custom
tools targeting Messages remain rejected. The gateway also offers opt-in
client tool search via `--client-tool-search function`, including SSE with
bounded per-call argument buffering; it does not execute
searches or emulate hosted search. See [custom tools and client search](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/custom-tools.md)
for the provider-support requirements and exact limits.

User images support URL/base64 references in ordered text/image messages on all
six request routes. Explicit detail selectors, provider-owned file IDs and
image tool results have portability limits; see [image inputs](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/images.md).

## Gateway

Provide credentials through environment variables:

```powershell
$env:PROTOVERTA_UPSTREAM_KEY = "your-upstream-key"
$env:PROTOVERTA_LOCAL_TOKEN = "a-long-random-local-token"
python -m protoverta --upstream-protocol chat --upstream-base-url https://provider.example/v1 --upstream-key-env PROTOVERTA_UPSTREAM_KEY --inbound-token-env PROTOVERTA_LOCAL_TOKEN --port 8765
```

The gateway accepts `POST /v1/chat/completions`, `POST /v1/messages`, and `POST /v1/responses`. It binds to `127.0.0.1`, prints its URL on startup, and requires `Authorization: Bearer <local-token>`. Use `--auth-style x-api-key` for upstreams that require that header.

`--upstream-reasoning provider_default` preserves client settings (the default).
Choose `disabled` to override effort with `none` on Chat/Responses or disabled
thinking on Messages. For OpenRouter, choose `openrouter_disabled`, which sends
`reasoning: {enabled: false}` on Chat/Responses instead. These explicit overrides
apply after conversion, also on native routes; they request disabling computation,
not hiding reasoning output. Models requiring reasoning may reject them; the
gateway does not retry with a different setting or discard unsupported output.
The equivalent Python option is `GatewayConfig(..., upstream_reasoning="openrouter_disabled")`.

`--reasoning-output omit` explicitly drops upstream reasoning that a translated
route cannot represent (never fabricated or forwarded); the default `reject`
fails instead. Many current models reason on every turn and reject or ignore a
disable request, so the launchers' `auto` policy uses `omit` on translated routes.
See [the reasoning contract](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/reasoning.md).

`--public-reasoning` separately opts into unsigned Chat `reasoning_content` ↔
Responses `reasoning_text` conversion for JSON/SSE and explicit history replay.
It never turns summaries into raw reasoning, fabricates Messages signatures,
or migrates encrypted state. See [the reasoning contract](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/reasoning.md).

Streaming supports all six directions for text and client-side function calls. Chat/Responses → Messages requires the explicit `--messages-usage-policy deferred` option (or `StreamTranscoder(..., messages_usage_policy="deferred")`). Initial usage is provisionally zero, then corrected with measured usage in `message_delta`; final input and output counts are mandatory. Overlapping tool calls are serialized in first-start order with bounded buffering. The default `strict` policy still rejects these two routes.

Native streams preserve feature payloads, including thinking/signatures, while validating framing and lifecycle. Successful terminal events are held until upstream EOF is verified. Cross-protocol conversion rejects unsupported content, annotations and interrupted streams. Explicitly requested [text logprobs](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/text-logprobs.md) are supported between Chat and Responses; unrequested probability data remains an error. The upstream base URL is the exact API prefix: include `/v1` or another version when required; Protoverta only appends the endpoint suffix. Native upstream API errors and allowed retry headers are preserved.

Usage normalizes total input tokens, including Messages cache reads and writes, and preserves reported cache/reasoning subtotals where the target can represent them. Unknown subtotals are omitted; measured zero remains zero. Strict Responses SDKs can reject incomplete usage details, including Messages → Responses because Messages does not report a reasoning-token subtotal. See [SDK compatibility limits](https://github.com/HaianCao/Protoverta/blob/main/docs/development/conformance.md). Interim deferred usage is not accurate context accounting. Effort levels `low`, `medium` and `high` map between each API's effort option; models determine their effect and availability.

For Codex's stateless text/function subset, opt in with `--client-profile codex`. This policy omits client telemetry, cache hints and the encrypted-reasoning include hint. It requires reasoning summaries to be disabled, maps initial developer instructions to Messages system text, and accepts an explicit Messages fallback limit via `--default-max-output-tokens`. The developer-to-system mapping is a compatibility policy; the two roles do not have guaranteed identical instruction priority. Hosted tools and hidden reasoning replay remain unsupported. The default `strict` profile does not perform these client-specific adjustments.

`--namespace-tools flatten` explicitly converts Responses function namespaces to
deterministic, collision-checked flat aliases for Chat/Messages. Each request
rebuilds its own mapping from definitions and historical calls; JSON responses
and SSE call items restore the original `name` and `namespace`, preserving call
IDs and arguments. Namespace descriptions are prepended to function descriptions.
Nested namespaces and custom/strict/hosted tools remain unsupported. An explicit
gateway extension can select one declared namespaced function. The default is
`reject`; native forwarding is unchanged.
Use `GatewayConfig(..., namespace_tools="flatten")` from Python.

For long or nonportable ordinary function names, opt into `--tool-names alias`
(`tool_names="alias"` in Python). Aliases are request-local and reversible for
definitions, history, forced choice, JSON and SSE; native forwarding is unchanged.
The explicit Codex/Claude compatibility profiles select aliasing by default;
`--tool-names reject` overrides that choice. Standalone strict mode stays strict.
See [function-name and identity rules](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/tool-names.md).

Forced function selection maps between all three protocols. JSON object mode maps
between Chat and Responses; JSON Schema maps across the supported strict subset
when Messages is involved. See [tool selection and output formats](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/output-formats.md).

An optional [capability registry](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/capabilities.md) can block
unsupported provider/model features before upstream I/O. Configure it with
`--capabilities-file FILE`; add `--require-verified-capabilities` to reject unknown
features too. Records are scoped to the exact API prefix, protocol and model.

Version-2 capability records can also declare output-token limits, context-window
metadata and allowed reasoning efforts. Explicit incompatible budgets/efforts
are rejected, not silently reduced. See the registry documentation for scope.

Optional [runtime policies](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/runtime.md) provide exact-model
wire profiles, cross-process request budgets and bounded pre-response HTTP
retries. These remain off unless explicitly configured; no model fallback or
stream replay is introduced.

The gateway bounds active requests with `--max-concurrent-requests` (default 16)
and socket I/O with `--timeout` (default 300 seconds; `--progress-timeout` 300 s and
`--total-timeout` 3600 s bound idle and whole-request time). See
[failure mapping and resource limits](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/errors.md).

For payload-free local request diagnostics, add `--diagnostics`. JSON lines go
to stderr only; no log file is created. Each response has a fresh
`X-Protoverta-Request-ID`. See [diagnostics and privacy](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/observability.md)
for supported fields and post-header SSE failure handling.

For a Messages model whose provider explicitly supports mid-conversation system
text, opt in with repeatable `--messages-system-model EXACT_MODEL_ID` or
`GatewayConfig(..., messages_system_models=("EXACT_MODEL_ID",))`. No model is
enabled by default. Instructions stay in place and must follow a complete user
turn (including all tool results) and precede assistant or end the array. The
Codex profile maps developer text to system text only under this explicit policy;
this does not make every Codex model-switch transcript valid. The library-level
equivalent is `convert_request(..., messages_mid_system=True)` after capability
verification. See [the placement rules](https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages).

For Claude Code translation, use `--client-profile claude --messages-usage-policy deferred`. Disable thinking, experimental betas, structured output and tool search in the client before sending requests. The profile omits cache hints and client metadata, accepts the SDK's `beta=true` query marker without forwarding it to another protocol, and represents tool-result errors as explicitly labelled text. Thinking/signature blocks, compaction and hosted tools remain unsupported. Native Messages does not apply this profile's rewriting. See [the streaming policy decision](https://github.com/HaianCao/Protoverta/blob/main/docs/decisions/ADR-001-messages-streaming-rejection.md).

The Codex profile also omits validated whitespace-only assistant placeholders
between a function call and its result when targeting Chat. Meaningful post-call
text, annotations and unknown fields are not discarded; strict conversion without
this client profile remains unchanged.

## Contributing

PRs should include sanitized fixtures and tests for both directions of any protocol change. Never commit API keys or private conversations.

```powershell
python -m pip install -e ".[test,typing]"
python -m pytest
python -m ruff check src tests sdk_tests scripts
python scripts/check_types.py
```

The strict type gate currently covers seventeen runtime/boundary modules, not every
codec or launcher. See [the typing scope](https://github.com/HaianCao/Protoverta/blob/main/docs/development/typing.md).

For independent official-SDK stream tests, install the optional `sdk-test` extra
and run `python scripts/check_sdk.py`. The gate uses only loopback fixtures and
no provider keys; see [conformance scope and findings](https://github.com/HaianCao/Protoverta/blob/main/docs/development/conformance.md).

Installed CLI clients can be exercised without provider keys. The smoke harness uses a loopback fixture, temporary client state and two read-only tool calls:

```powershell
python scripts/smoke_clients.py --client codex --upstream chat --tool-loop
python scripts/smoke_clients.py --client claude --upstream messages --tool-loop
python scripts/smoke_clients.py --client claude --upstream chat --tool-loop
python scripts/smoke_clients.py --client claude --upstream responses --tool-loop
```

Use `--adapter-source PATH` to include a source launcher, or `--installed-adapter` to test a launcher installed in the active Python environment. These fixtures verify client/protocol interoperability, not a real provider's model capabilities.

Provider checks and limits: [tested providers and known limits](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/providers.md)
(OpenRouter, NVIDIA NIM, TokenHarbor, AgentRouter) and
[OpenRouter text/tool conformance](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/openrouter.md).
Session replay checks and boundaries: [resume compatibility](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/resume.md).

For an opt-in real OpenRouter text round trip through a source launcher, use
`scripts/probe_clients.py --client claude --upstream chat --model MODEL_ID --key-env KEY_VARIABLE --adapter-source PATH --allow-live`.
This uses temporary client state, explicitly disables upstream reasoning, and may
incur API charges. Add `--tool-loop` to verify Claude's read-only nonce-file tool
round trip, or Codex's read-only `get_goal` query with its matching tool result
observed in the outbound request. Shell permissions are not relaxed. These probes
do not test resume unless `--resume-target` is provided; never use private prompts as fixtures.

For a two-turn real resume probe, use `--resume-target PROTOCOL`. Combine it with
`--tool-loop` to resume after a completed read-only tool call. The selected model is retained, while the second invocation uses
a different provider configuration ID and must recall a token from the first
turn. This still requires `--allow-live` and may incur API charges.

See [SECURITY.md](https://github.com/HaianCao/Protoverta/blob/main/SECURITY.md) for reporting security issues. Licensed under [MIT](https://github.com/HaianCao/Protoverta/blob/main/LICENSE).

Optional gateway controls: [exact route evidence](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/route-evidence.md),
[bounded model catalogs](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/catalogs.md), and
[context preflight / local soak](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/context-preflight.md).

Provider integration: [declarative profiles, route doctor and session diagnostics](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/advanced-adapters.md).
Extended contracts: [token counting, prompt caching, PDF and native compaction](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/counting-caching-media.md).
Operational contracts: [HTTP boundaries and count errors](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/request-boundaries.md),
[explicit routing and token quotas](https://github.com/HaianCao/Protoverta/blob/main/docs/compatibility/routing-and-token-quotas.md).
Development evidence: [versioned validation matrix](https://github.com/HaianCao/Protoverta/blob/main/docs/development/validation-matrix.md)
and [current OpenResponses schema gaps](https://github.com/HaianCao/Protoverta/blob/main/docs/development/openresponses-audit.md).
Latest local source/wheel acceptance: [P14 results and remaining gates](https://github.com/HaianCao/Protoverta/blob/812134e/docs/development/p14-packaging-acceptance-2026-11-04.md).
Isolated acceptance: [local preview](https://github.com/HaianCao/Protoverta/blob/main/docs/development/local-preview.md),
[harness tools and session MCP](https://github.com/HaianCao/Protoverta/blob/main/docs/development/harness-tools.md).
