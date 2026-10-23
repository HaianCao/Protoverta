# Tool selection and output formats

## Forced function selection

The converter maps a declared function by its exact name:

| Chat | Messages | Responses |
| --- | --- | --- |
| `{"type":"function","function":{"name":"add"}}` | `{"type":"tool","name":"add"}` | `{"type":"function","name":"add"}` |

`auto`, `none`, and required tool use are also mapped. Messages uses `any` for
required selection. Explicit sequential/parallel settings are preserved. The
converter rejects undeclared names, duplicate definitions, hosted-tool choices
and unknown selection fields. Tool definitions are not pruned or renamed except
under the separate namespace alias policy. Selection support remains model-specific.

With `namespace_tools="flatten"`, the gateway additionally accepts the explicit
Protoverta extension `{"type":"function","namespace":"math","name":"add"}`
on a Responses request. It resolves this pair against declared namespace tools
and forces the corresponding flat alias upstream. This `namespace` choice field
is a gateway extension, not a claim about the native OpenAI tool-choice schema.
It is not added to requests sent to a native Responses upstream.

## Structured output

| Format | Chat ↔ Responses | Routes involving Messages |
| --- | --- | --- |
| Plain text | Supported | Supported |
| JSON object mode | Supported | Rejected: no equivalent option |
| JSON Schema | Preserves schema, name, description and explicit strict setting | Explicit strict mode and the shared schema subset below |

Wire locations are Chat `response_format`, Responses `text.format`, and Messages
`output_config.format`. Messages effort and format share `output_config`; a
reasoning override removes effort while retaining the format.

Chat/Responses schema options map without schema rewriting. Across the Messages
boundary, the current subset requires an object root and supports primitive
types (including nullable type arrays), nested objects/arrays, scalar enums,
`anyOf`, and schema-level titles/descriptions. Every object property must be
required and `additionalProperties` must be false. References, recursive schemas,
numeric/string/array constraints and other unsupported keywords are rejected.
Depth is bounded at 32. Constraints are never moved into prompts or stripped.

Messages structured output implies constrained schema generation. A Chat or
Responses request must therefore explicitly set `strict: true` to translate to
Messages. Non-strict/default schema mode is not upgraded silently. Messages
schemas with optional properties cannot translate to OpenAI's strict subset.

Messages has no output-format name field. The identifier is omitted when
targeting Messages, and `protoverta_output` is generated when translating a
Messages format to Chat/Responses. This changes only the format label. An outer
format description cannot be preserved and is rejected on the Messages route;
put descriptions inside the schema. Within Chat ↔ Responses the label and
description are preserved.

The gateway restores the incoming Responses `text` configuration in generated
JSON responses and streaming response snapshots. Output text remains text in
each wire protocol; no parsed JSON object is injected into the response body.
Schema enforcement is the upstream provider's responsibility. Protoverta does
not buffer a stream for local schema validation, repair JSON, fabricate success
after a refusal, or hide token-limit truncation. Native forwarding is unchanged.

Live synthetic checks can be reproduced with `scripts/probe_formats.py --source
responses --upstream chat --stream --model MODEL_ID --key-env KEY_VARIABLE
--allow-live`. The script requires explicit live-call acknowledgement and only
checks a fixed synthetic schema. See [the recorded provider results](openrouter.md).

References checked 2026-10-23:

- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Anthropic tool selection](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
