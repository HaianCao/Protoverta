# Session replay compatibility

Verified locally on 2026-10-23 with Codex CLI 0.163.0 and Claude Code 2.1.308,
both through their source launchers and the production gateway. The upstreams
are scripted loopback SSE fixtures, not real provider/model endpoints.

The harness starts a persistent session, exits the CLI, starts it again with
the same session ID, and observes the next upstream request. A passing case
requires the prior assistant answer in that request, the newly selected model
and protocol, the same session ID, and a successful final response.
Each gateway launch uses a new local credential/listener. All sessions and
configuration live under a temporary directory; no existing user sessions are
read, modified or migrated. No API keys or external model calls are required.

## Observed matrix

These results cover text-only history, without hidden reasoning or tool state.
"Pass" describes the fixture/client behavior, not universal model compatibility.
Provider changes mean a different launcher provider configuration ID; credentials
and upstreams in this test are synthetic.

| Scenario | Codex | Claude |
| --- | --- | --- |
| Chat, same provider/model | Pass | Pass |
| Chat, different model | Pass | Pass |
| Chat, different provider ID | Pass | Pass |
| Chat → Messages, different provider and model | Rejected: mid-conversation instructions | Pass |
| Chat → Responses, different provider and model | Pass | Pass |
| Messages → Chat, different provider and model | Pass | Pass |
| Messages → Responses, different provider and model | Pass | Pass |
| Responses → Chat, different provider and model | Pass with namespace mapping | Pass |
| Responses → Messages, different provider and model | Rejected: mid-conversation instructions | Pass |
| Responses → Messages, different provider, same model | Pass with namespace mapping | Not separately tested |

Additional Codex controls passed: changing both provider/model on Chat, resuming
Messages without changes, and Chat → Messages while retaining the model.
Changing only the model while remaining on Messages was rejected because Codex
injected developer instructions after the conversation had started.

Rejections are current adapter capability boundaries, not successful resumes
and not proof that these features are impossible to adapt. Protoverta's portable
Messages baseline only maps initial system/developer instructions; moving later
developer messages to that field would change their position/meaning.
Some current Claude models support mid-conversation `role: "system"` messages
under model-specific placement rules. Protoverta now offers an explicit exact-model
allowlist for that extension, while still rejecting invalid placement. Codex's
observed post-assistant `model_switch` placement does not meet those rules.
Support on arbitrary compatible
providers, including the selected OpenRouter Nemotron model, is not established.
See [Anthropic's current capability documentation](https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages).

Shape-only traces confirmed a developer message tagged `model_switch` after
the conversation history when Codex changed models. Another trace confirmed
`type: "namespace", name: "multi_agent_v1"` in the resumed request's tool
definitions, with function children, despite the translated launcher's
multi-agent setting being disabled. This proves the request shape; it does not
establish whether Codex persisted or rebuilt that definition internally.
The translated Codex launcher now enables reversible, request-scoped namespace
aliases for synchronous function tools. Follow-up fixture/real-CLI checks passed
Responses → Chat with changed provider/model and Responses → Messages with a
changed provider and unchanged model. Schemas and history are retained, not
removed to force replay. JSON/SSE namespace tool round trips are also covered
with synthetic upstreams; no subagent tool was executed by the CLI probes.
The gateway returns an actionable error before forwarding the incompatible
request. Codex may repeat the local failed request under its own retry policy.

For these cases, retain a compatible native provider/model or start a fresh
session. There is no automatic transcript extraction, lossy handoff, session-file
rewriting, or model fallback.

## Scope and regression coverage

Complete text/function-call/tool-result history is unit-tested across all six
conversion directions, including stable call IDs and semantic JSON arguments.
The original matrix above covers text history. Follow-up CLI checks also passed
after a completed read-only tool call: Codex Chat → Chat with provider/model
changes, Codex Chat → Messages retaining the model, Claude Chat → Responses,
and Claude Responses → Messages with provider/model changes. These require the
matching tool call/result ID and previous answer in the resumed request, the
same session ID, and the final response. Tools were `get_goal` (Codex) and a
temporary fixture file `Read` (Claude); no shell permissions were weakened.
All four use scripted upstreams, not real-provider resume.

## Real-provider resume

Two follow-up text resume probes passed on OpenRouter with
`nvidia/nemotron-3-ultra-550b-a55b:free`, using production
`upstream_reasoning="openrouter_disabled"`:

- Codex: Chat → Messages, same model and a different provider configuration ID.
- Claude: Chat → Responses, same model and a different provider configuration ID.

Each started a persistent temporary session, supplied a random token only in
the first user message, exited, resumed against the target protocol, and required
the final assistant answer to recall that token under the same session ID.
Both used the same OpenRouter vendor/key and model; they do not verify changing
real vendor credentials or changing to a different real model. The prompts were
synthetic, tools were not used, and existing user configuration/session files
were untouched. No production transport rewriting was injected.

Follow-up real-provider checks also passed after a completed read-only tool
call on the same model/vendor and a changed provider configuration ID:

- Codex: `get_goal`, then Chat → Messages resume.
- Claude: `Read` on a synthetic temporary file, then Chat → Responses resume.

The probes required a real tool completion, unchanged session ID and exact
nonce recall in the resumed answer. They did not verify changing the real model
or vendor, hidden reasoning, or every protocol pair. Reproduce with
`scripts/probe_clients.py --tool-loop --resume-target TARGET --allow-live` and
the corresponding client, source protocol, adapter source and key environment.

Encrypted reasoning,
signed thinking, server-owned `previous_response_id`, image tool results and
compaction are not portable under the current contract and remain errors.
Ordinary user image history now has a [limited portable contract](images.md);
the original resume matrix did not exercise it. Unsigned public reasoning has
an explicit [Chat/Responses extension](reasoning.md), not hidden-state migration.
Native forwarding is not a promise that another provider can reuse such state.

## Additional real-vendor observations (2026-10-23)

Text resume from OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` (Chat,
reasoning explicitly disabled) to NVIDIA NIM Chat passed with Claude Code for
`minimaxai/minimax-m3.1-flash` and `deepseek-ai/deepseek-v4.1-flash`. Both changed
the real vendor, credentials and model, retained the session ID and recalled a
random token supplied only before the switch. These were text-only samples;
the target retained provider-default reasoning and happened not to return an
unsupported thinking block. This does not guarantee all future turns.

Codex's switch to the same NIM MiniMax target initially failed with a recorded
`unsupported_reasoning` decoder category. With the explicit unsigned public
reasoning extension enabled, the two-turn switch passed with the same session
ID and exact token recall. No stream payload or session file was rewritten by
the test harness; only fixed diagnostic categories/shapes were recorded.

A follow-up three-turn Codex probe passed after receiving public reasoning:
the third request replayed a public reasoning item, kept the same session and
recalled the token again. Codex's serialized `encrypted_content: null` is
accepted as an absent placeholder; any non-null value remains rejected. This
does not verify signed/encrypted reasoning or cross-model reasoning-state reuse.

The same three-turn Codex vendor/model switch also passed after a completed
read-only `get_goal` call on the source. The probe verified the tool result,
then a target public-reasoning response, then its replay on a third turn,
unchanged session ID and exact token recall. No goal was created or updated.

NIM `z-ai/glm-5.3-flash` passed a direct short Chat JSON baseline but its Claude
resume probe timed out, so resume is unverified. `openai/gpt-oss-20b` failed two
direct Chat JSON baselines with the coarse `transport_or_json_error` category;
these do not distinguish timeout/network failure from invalid JSON, so it has
no passing new integration result.
OpenRouter `thinkingmachines/inkling:free` returned 403 on a real Claude resume
attempt, reporting a harness-access restriction. It was not retried with fake
app identity or a paid fallback. These are failures/unverified cases, not passes.

NIM's GLM page uses `/glm-5-3-flash` in its URL, while its documented API model
ID is `z-ai/glm-5.3-flash`. See the
[NVIDIA sample](https://build.nvidia.com/z-ai/glm-5-3-flash).

`scripts/probe_clients.py` accepts `--target-base-url`, `--target-key-env`,
`--target-model`, `--source-reasoning`, `--target-reasoning` and the Codex-only
`--target-public-reasoning`. The source/target keys stay in environment variables;
the report exposes only whether credentials differ. `--verify-replay` adds a
third target turn and separately reports whether the client sent public
reasoning items back. Fixed stream-error categories do not retain bodies.

Reproduce a supported case from a checkout with Protoverta installed:

```powershell
python scripts/smoke_resume.py --client claude --source chat --target responses --adapter-source ../free-claude-cli --change-provider --change-model
python scripts/smoke_resume.py --client codex --source messages --target chat --adapter-source ../free-codex-cli --change-provider --change-model
python scripts/smoke_resume.py --client claude --source chat --target responses --adapter-source ../free-claude-cli --change-provider --change-model --tool-loop
```

The script exits nonzero for an unsupported history and labels it
`unsupported_history`; it does not count that as a pass. Unexpected failures
are labelled `failed`. CLI installation is required; the normal unit suite does
not install or execute either CLI.

For synthetic Codex diagnostics, add `--trace`. It records only model name,
developer-message positions/XML tag names, and tool types/names. Instruction
contents, tool schemas, headers and credentials are not recorded. Trace files
are temporary and only the first shape summary is returned.

References: [Codex resume commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
[Claude CLI resume flags](https://code.claude.com/docs/en/cli-reference).
