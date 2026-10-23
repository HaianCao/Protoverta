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
| Responses → Chat, different provider and model | Rejected: namespaced tools | Pass |
| Responses → Messages, different provider and model | Rejected: namespaced tools | Pass |

Additional Codex controls passed: changing both provider/model on Chat, resuming
Messages without changes, and Chat → Messages while retaining the model.
Changing only the model while remaining on Messages was rejected because Codex
injected developer instructions after the conversation had started.

Rejections are current adapter capability boundaries, not successful resumes
and not proof that these features are impossible to adapt. Protoverta's portable
Messages baseline only maps initial system/developer instructions; moving later
developer messages to that field would change their position/meaning.
Some current Claude models support mid-conversation `role: "system"` messages
under model-specific placement rules, but Protoverta does not implement a
capability-gated mapping for that extension. Support on arbitrary compatible
providers, including the selected OpenRouter Nemotron model, is not established.
See [Anthropic's current capability documentation](https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages).

Shape-only traces confirmed a developer message tagged `model_switch` after
the conversation history when Codex changed models. Another trace confirmed
`type: "namespace", name: "multi_agent_v1"` in the resumed request's tool
definitions, with function children, despite the translated launcher's
multi-agent setting being disabled. This proves the request shape; it does not
establish whether Codex persisted or rebuilt that definition internally.
Protoverta does not yet flatten namespaces with a reversible call-name mapping,
and does not remove those tools to force replay.
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

## Real-provider text resume

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

Real-provider resume **after** tool calls is still not covered. The real-provider
fresh-session tool checks and the scripted tool-history resume checks are
separate evidence; neither proves that remaining combination.

Encrypted reasoning,
signed thinking, server-owned `previous_response_id`, multimodal history and
compaction are not portable under the current contract and remain errors.
Native forwarding is not a promise that another provider can reuse such state.

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
