# ADR-001: Explicit policy for cross-protocol Messages streaming

Status: accepted; strict rejection is retained, not a universal impossibility.

## Context

Chat Completions and Responses generally report usage at completion. Messages
starts with a usage object. The Anthropic SDK can apply cumulative input and
output usage updates from `message_delta`. Requiring measured initial usage
therefore prevents an exact incremental mapping, but not a useful opt-in bridge.

## Decision

- Default `messages_usage_policy="strict"` rejects Chat/Responses to Messages SSE.
- Explicit `"deferred"` mode emits zero provisional initial usage and replaces it
  with measured final usage, including cache breakdowns when supplied. Zero is
  a compatibility placeholder, not a tokenizer measurement.
- Final measured input and output usage are mandatory. Missing usage, malformed
  arguments, upstream errors and premature EOF never emit successful completion.
- Text is incremental. Overlapping function calls are serialized in first-start
  order with bounded pending-block buffering, preserving IDs and arguments.
- Terminal events are withheld until clean upstream EOF. This is not a buffered
  JSON response replayed as if it had streamed incrementally.
- Claude client compatibility is a separate opt-in profile. Telemetry/cache hints
  are omitted, tool-result errors become labelled text, and the launcher disables
  thinking and unsupported experimental capabilities for translated sessions.
  Existing thinking/signature, hosted-tool, multimodal and compaction payloads
  remain errors. Native Messages keeps its original payloads.

## Consequences

Clients must tolerate provisional initial usage and final correction. Interim
usage cannot be used as accurate context accounting. Native routing is preferred
when full provider-specific functionality is required. Validate actual client
versions with multi-tool smoke fixtures; these do not prove model capabilities.
