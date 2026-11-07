# Provider compatibility and known limits

Evidence date: **2026-11-06**. Clients: Claude Code **2.1.318** (ClaudApter) and
Codex CLI **0.167.0** (CodexApter), Protoverta built from source. "Tool loop"
means a real model call, a real client tool execution and a second model call
that must use the tool result. Provider behaviour changes without notice;
re-check with the live probes below before relying on a row.

This page is written for the user documentation site. Each provider section
has the same headings: endpoints, tested models, recommended settings, limits.

## Summary matrix

| Provider | Model | Upstream protocol | Claude Code (ClaudApter) | Codex (CodexApter) |
| --- | --- | --- | --- | --- |
| OpenRouter | `stealth/crouton-alpha` | Chat | Tool loop passed | Tool loop passed |
| OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | Responses / Messages | Tool loop passed (Responses) | Tool loop passed (Messages) |
| NVIDIA NIM | `z-ai/glm-5.3-flash` | Chat | Tool loop passed | Gateway tool loop passed |
| NVIDIA NIM | `deepseek-ai/deepseek-v4.1-flash` | Chat | Tool loop passed | Works with effort `low`/`high`; `medium` rejected by provider |
| NVIDIA NIM | `minimaxai/minimax-m3.1-flash` | Chat | Gateway tool loop passed | Tool loop passed |
| TokenHarbor | `deepseek-v4.1-flash:free` | Chat, Responses, Messages | Tool loop passed on all three (Responses needs 0.3.0a11) | Tool loop passed on all three |
| AgentRouter | `gpt-5.6-sol` | Responses (direct only) | Not usable through the gateway | Direct mode: tool loop passes (2026-11-07) |

"Gateway tool loop passed" rows were verified with a scripted client of the
same wire shape (two-turn tool loop through the real provider), not the CLI.

All translated rows use the launchers' automatic reasoning policy (see
[reasoning](reasoning.md)): the model keeps its default reasoning and reasoning
output the client cannot represent is omitted, never fabricated.

## Behaviour that applies to every provider

- **Reasoning is usually on and often cannot be turned off.** A "reasoning off"
  request is not portable: some models reject it (HTTP 400), others ignore it.
  The automatic policy therefore does not send one. Choose an explicit
  `disabled` reasoning policy only for a model known to honour it.
- **Hidden reasoning still costs tokens.** Omitted reasoning is billed by the
  provider and counted in output usage, but is not shown in Claude Code.
- **Context window and output size are not discovered.** Claude Code and Codex
  assume their own sizes for unknown model IDs. Set the provider's model limits
  in the setup page (ClaudApter: context window and max output; CodexApter:
  context window; Messages providers in CodexApter also have an output limit,
  default 16384).
- **Errors from the provider are passed through.** If the provider reports an
  error inside an HTTP 200 stream, the client receives the provider's message
  with its 4xx status (non-retryable) instead of a generic stream error.
- **Strict stream contract.** Non-standard Chat streams are rejected; see
  [errors](errors.md#chat-stream-variants-that-translated-routes-reject).
- **Claude Code auto mode works behind the gateway (2.1.319, live).** The
  2.1.319 changelog says auto mode asks or denies calls its server-side review
  cannot check behind gateways. A live check on 2026-11-07 (OpenRouter,
  `nvidia/nemotron-3-ultra-550b-a55b:free`) found otherwise: a `Bash` file
  write ran in auto mode both directly and through the gateway's Messages→Chat
  route with ClaudApter's settings, and was refused in the default mode. How
  Claude Code reviewed the call is not visible; recheck after CLI updates.

## OpenRouter

- **Endpoints:** `https://openrouter.ai/api/v1` with `/chat/completions`,
  `/responses` and `/messages`. Bearer authentication.
- **Tested models:** `stealth/crouton-alpha` (Chat), `nvidia/nemotron-3-ultra-550b-a55b:free`
  (Chat, Responses, Messages).
- **Recommended:** ClaudApter → protocol `messages` (native) or `responses`/`chat`
  in gateway mode with automatic reasoning. CodexApter → `responses` (native) or
  `chat`/`messages` in gateway mode with automatic reasoning.
- **Limits:**
  - `stealth/crouton-alpha` has **mandatory reasoning**: `reasoning_effort: none`
    and `reasoning: {enabled: false}` both return HTTP 400 "Reasoning is
    mandatory for this endpoint and cannot be disabled". Do not select the
    `disabled` or `openrouter_disabled` policy for it.
  - Chat streams carry reasoning as `reasoning` plus structured
    `reasoning_details`; the details may contain provider-specific or encrypted
    data and are always omitted on translated routes. Unsigned `reasoning` text
    is kept for Codex as Responses `reasoning_text`.
  - OpenRouter repeats the finished choice in a usage postlude; this is
    accepted. See also [OpenRouter conformance](openrouter.md).
  - Free (`:free`) models are rate limited by OpenRouter; expect HTTP 429 with
    `Retry-After` under load.

## NVIDIA NIM (integrate.api.nvidia.com)

- **Endpoint:** `https://integrate.api.nvidia.com/v1/chat/completions` only
  (Chat). Bearer authentication.
- **Tested models:** `minimaxai/minimax-m3.1-flash`,
  `deepseek-ai/deepseek-v4.1-flash`, `z-ai/glm-5.3-flash`.
- **Recommended:** gateway mode, protocol `chat`, automatic reasoning.
- **Limits:**
  - `minimax-m3.1-flash` and `glm-5.3-flash` **ignore** `reasoning_effort: none`
    and still stream `reasoning_content`.
  - `deepseek-v4.1-flash` honours `none`, `low` and `high` but answers
    `reasoning_effort: medium` with HTTP 200 followed by an error event
    ("Invalid request"). In Codex choose `low` or `high` effort for this model;
    with `medium` the request fails with that message (HTTP 400). A capability
    registry record can declare the allowed efforts to reject `medium` before
    any provider call.
  - Free endpoints are rate limited per model; HTTP 429 ("Too Many Requests")
    was observed after a few quick requests to `minimax-m3.1-flash`.
  - Image inputs follow each model's own support; only text and tools were
    tested here.

## TokenHarbor

- **Endpoints:** `https://tokenharbor.ai/v1` serves Chat Completions,
  Responses and Messages (all three checked on 2026-11-07). Bearer
  authentication.
- **Tested model:** `deepseek-v4.1-flash:free`.
- **Recommended:** gateway mode with any of the three protocols, automatic
  reasoning. Live two-tool loops pass for Claude Code on Chat, Responses and
  Messages, and for Codex on Chat, Responses and Messages.
- **Limits:**
  - Chat streams `reasoning_content`; honours `reasoning_effort: none`. Standard
    OpenAI stream shape with final usage and `[DONE]`.
  - Responses streams omit the `logprobs` field on text parts and on the final
    text events. When logprobs were not requested, Protoverta 0.3.0a11
    and later read the absent field as empty, so no data is lost;
    0.3.0a10 and earlier reject the stream with "unsupported Responses content
    part metadata" (HTTP 502), which breaks Claude Code on the Responses route.
    Requesting logprobs from the Responses endpoint still fails, because none
    are returned there.
  - The reported model name drops the `:free` suffix (`deepseek-v4.1-flash`);
    this is accepted.

## AgentRouter

- **Endpoint:** `https://agentrouter.org/v1` (Responses and Chat).
- **Tested model:** `gpt-5.6-sol`.
- **Limits:**
  - AgentRouter accepts only its supported official clients. Any other client
    (including the Protoverta gateway) receives HTTP 401 "unauthorized client
    detected". The gateway does not imitate another client, so **gateway mode,
    Chat Completions and ClaudApter cannot be used with AgentRouter**.
  - **Supported setup:** CodexApter, protocol `responses`, execution mode
    `direct` (Codex talks to AgentRouter itself). On 2026-11-07, `codex-ca exec`
    with Codex 0.167.0 completed a tool round trip twice (9 s each; the
    session rollout records the tool call and its output before the answer).
  - **Retries can land on another backend.** In one longer run, after Codex's
    stream reconnect ("Reconnecting... 1/5") AgentRouter answered "The
    requested item was created under a different Azure OpenAI resource" and the
    turn failed. Requests that refer to earlier response items only work on
    the backend that created them, and AgentRouter does not keep a session on
    one backend. Re-run the prompt; this cannot be fixed on the client side.

## Live re-check (2026-11-07)

Protoverta 0.3.0a10 plus the fixes released in 0.3.0a11, Claude Code **2.1.319**
(ClaudApter) and Codex CLI **0.167.0** (CodexApter). Two-tool loops, automatic
reasoning policy.

| Client | Route | Provider | Model | Result |
|---|---|---|---|---|
| Claude Code | Messages→Chat | OpenRouter | `stealth/crouton-alpha` | pass (37 s) |
| Claude Code | Messages→Chat | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | pass (8 s) |
| Claude Code | Messages→Responses | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | pass (6 s) |
| Claude Code | Messages→Chat | NVIDIA NIM | `minimaxai/minimax-m3.1-flash` | pass on retry (122 s); first run hit the 240 s client limit |
| Claude Code | Messages→Chat | NVIDIA NIM | `deepseek-ai/deepseek-v4.1-flash` | pass (188 s) |
| Claude Code | Messages→Chat | NVIDIA NIM | `z-ai/glm-5.3-flash` | pass (24 s) |
| Codex | Responses→Chat | OpenRouter | `stealth/crouton-alpha` | pass (18 s) |
| Codex | Responses→Messages | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | pass (18 s) |
| Codex | Responses→Chat | NVIDIA NIM | `minimaxai/minimax-m3.1-flash` | pass (161 s) |
| Codex | Responses→Chat | NVIDIA NIM | `deepseek-ai/deepseek-v4.1-flash` | pass (150 s) |
| Codex | Responses→Chat | NVIDIA NIM | `z-ai/glm-5.3-flash` | pass (7 s) |
| Codex | Responses, direct | AgentRouter | `gpt-5.6-sol` | pass, 2 of 2 (9 s each) |
| Claude Code | Messages→Chat | TokenHarbor | `deepseek-v4.1-flash:free` | pass (22 s) |
| Claude Code | Messages (native) | TokenHarbor | `deepseek-v4.1-flash:free` | pass (19 s) |
| Claude Code | Messages→Responses | TokenHarbor | `deepseek-v4.1-flash:free` | failed (502) before the absent-logprobs fix; pass 2 of 2 after it |
| Codex | Responses→Chat | TokenHarbor | `deepseek-v4.1-flash:free` | pass (20 s) |
| Codex | Responses (native) | TokenHarbor | `deepseek-v4.1-flash:free` | pass (19 s) |
| Codex | Responses→Messages | TokenHarbor | `deepseek-v4.1-flash:free` | pass (20 s) |

NIM's free tier was much slower than the day before (minimax and deepseek took
two to three minutes per loop). Give long-running clients a generous timeout;
the gateway's own defaults (300 s idle, 3600 s total) already allow this.

## Token logprobs (live, 2026-11-06)

Requested text logprobs (`include: ["message.output_text.logprobs"]`, two
alternatives) through a Chat upstream are **not certified for any tested
provider**; the gateway rejects data it cannot verify rather than repairing it.

| Provider / model | Observation | Result |
| --- | --- | --- |
| TokenHarbor `deepseek-v4.1-flash:free` | Returns logprobs, but alternative-token `bytes` are Unicode code points (`[22909, 30340]` for 好的), not UTF-8 bytes | Rejected: invalid byte arrays |
| NVIDIA NIM `deepseek-ai/deepseek-v4.1-flash` | No progress for 90 s when logprobs are requested (JSON and SSE) | `progress_timeout` |
| NVIDIA NIM `minimaxai/minimax-m3.1-flash` | No first byte within the probe's 15 s I/O timeout | Not established |
| OpenRouter `stealth/crouton-alpha` | HTTP 400 for the logprob request | Unsupported |
| OpenRouter `nemotron`, NIM `glm` (2026-11-04) | No records / reasoning-only records | Not certified |

Use `scripts/probe_logprobs.py --allow-live --timeout 90 --upstream-reasoning disabled`
to re-check a provider.

## Re-checking a provider

```powershell
# Real CLI tool loop through the source launcher (may incur API charges)
python scripts/probe_clients.py --allow-live --tool-loop --source-reasoning auto `
  --client claude --upstream chat --model MODEL_ID `
  --key-env KEY_VARIABLE --base-url https://provider.example/v1 `
  --adapter-source ../free-claude-cli
```

Use `--client codex --adapter-source ../free-codex-cli` for Codex. Results are
evidence for that provider, model, protocol, client version and date only.
