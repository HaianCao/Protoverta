# Custom tools and client tool search

## Native custom tools: completed JSON

Chat Completions and Responses both define custom tools. Protoverta maps their
definitions, forced choices, calls and text results directly in either direction:

- Chat `custom.format.grammar.{syntax,definition}` maps to Responses
  `format.{syntax,definition}` without rewriting the grammar.
- Raw `custom.input` maps to `custom_tool_call.input`, not JSON arguments.
- Call IDs and mixed function/custom call order survive history replay. Chat's
  untyped role-tool results inherit the type only from a preceding matching ID;
  explicitly mismatched Responses output types fail.
- Optional descriptions and absent/text/regex/lark formats remain distinct.
  Aliasing changes only protocol-defined names, never raw input or grammar.
- Messages has no equivalent custom/grammar contract and is rejected. Rich
  custom results containing images also cannot be relocated into Chat user
  messages. Namespaced custom tools remain outside namespace flattening.

Provider support must be recorded separately as `custom_tools`. An OpenAI
schema is not evidence that another Chat-compatible provider supports it.
No live provider certification was performed for this feature.

## Streaming boundary

The Responses custom-input delta/done/item lifecycle is decoded and validated
against accumulated input, item identity and terminal snapshots. Native gateway
forwarding remains byte-preserving and resource-bounded.

The published Chat streaming schema does not establish a custom delta wire.
Cross-protocol requests combining custom tools/history with `stream: true` are
therefore rejected before upstream I/O. Unexpected custom output in a translated
stream fails without a fabricated terminal success. Do not infer compatibility
from synthetic round-trip fixtures. Grammar is never silently converted into a
function's JSON schema.

## Explicit JSON client search policy

Set `GatewayConfig(..., client_tool_search="function")` or
`--client-tool-search function` to adapt Responses client-executed search to an
ordinary function on a Chat/Messages upstream. The default is `reject`; native
Responses forwarding is unchanged. Consumer setup controls do not expose this
new option yet.

This compatibility policy is **not exact native deferred-loading semantics**:
tools returned in explicit search history are exposed eagerly for the current
generation. It does not reproduce native cache or historical context placement.
The application remains responsible for searching, authorizing schemas and
executing any tools. Protoverta performs none of those actions.

The supported subset requires an explicit `execution: "client"`, an object
argument schema, and completed search call/output pairs with unique matching
IDs. The reserved function name is `pv_client_tool_search`; collisions fail.
Search arguments and results retain JSON objects, IDs and tool definitions.
Unloaded deferred definitions are not sent as callable tools. Loaded function,
custom or namespace definitions pass through the normal strict converter and
optional namespace policy; conflicting definitions are rejected.
Deferred namespace children stay unavailable until a matching completed search
output explicitly loads them. Immediate children and loaded subsets are merged
without dropping schema/description conflicts; an entirely unloaded namespace
is omitted from the callable upstream tool list.

Returned calls are restored to `tool_search_call` with object arguments and
client execution. Hosted/server search, missing execution, incomplete/unpaired
history, `additional_tools` injection and streaming search are not emulated.
No unspecified execution mode is guessed from a call ID. No fallback, retry or
external lookup is introduced.

## Sources

- [Chat definitions and completed calls](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Chat streaming schema](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
- [Responses custom-input events](https://developers.openai.com/api/reference/resources/responses/streaming-events)
- [Client-executed tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)

These references describe protocol shapes, not third-party provider guarantees.
