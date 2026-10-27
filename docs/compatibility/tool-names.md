# Function names and stream identity

Translated requests targeting Chat or Responses require function names containing
1-64 ASCII letters, digits, underscores or dashes. Unsupported names are rejected
before upstream I/O in the default strict profile, including historical calls. Namespace flattening
is a separate opt-in policy and does not replace ordinary function-name handling.

Use `--tool-names alias` or `GatewayConfig(..., tool_names="alias")` to opt into
request-scoped reversible aliases. Definitions, forced function choice and
historical call names are collected together. Existing portable names are
reserved; generated aliases are deterministic, collision-checked and within
the portable limit. Rebuild the map for each request, including when an old
function survives only in history. Mappings are not shared across sessions.

JSON function calls and streamed tool starts restore original names, including
generated Responses terminal snapshots. Descriptions, schemas, text, arguments,
tool results and call IDs are never searched/replaced. Unknown generated aliases
fail rather than being guessed. Namespace restoration composes after this policy.
Native routes are unchanged, even when aliasing is enabled.

Historical tool roles are validated before translated requests reach the
upstream. Calls belong to assistant turns; results belong to user/tool-result
turns (Chat uses its `tool` role, Messages uses a user `tool_result`, and
Responses uses a separate call-output item). A Chat `tool_calls` field on a
user/system/developer message is rejected even when empty or null. Malformed
roles are never repaired by moving content or dropping tool fields. Native
passthrough routes continue to leave provider validation to the native API.

The explicit `codex` and `claude` compatibility profiles enable this reversible
name policy by default, so existing source launchers using those profiles pick
it up with a compatible core build. Set `--tool-names reject` to override the
profile default. This does not relax reasoning, unknown-field or tool-state
validation, and does not rewrite tool arguments or execute tools.

Library users can explicitly retain the request context:

```python
from protoverta import ToolNameMap, convert_request, convert_response

names = ToolNameMap()
prepared = names.prepare(messages_request, "messages")
chat_request = convert_request(prepared, "messages", "chat")
# Send chat_request to the selected upstream yourself.
messages_response = names.restore(
    convert_response(chat_response, "chat", "messages"), "messages"
)
```

For SSE, pass `tool_name_transform=names.decode` to `StreamTranscoder`. This
changes only canonical tool-start names before encoding, keeping delta events
and snapshots consistent. Do not reuse one map across independent requests.

Chat stream tool identity remains strict: numeric nonnegative indexes identify
fragments, sparse indexes are supported, and IDs/names cannot change after a
call opens. Conflicting IDs, missing/boolean indexes and ambiguous fragments are
rejected. A provider-specific dialect with different fragmentation rules needs
an explicit, tested policy; no heuristic assignment is enabled by default.

These features are available in source. Existing launcher release pins are not
changed automatically; old published wheels do not gain them. No provider-specific
reasoning/signature conversion is added.
