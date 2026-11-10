# Using the library

## JSON requests and responses

{func}`protoverta.convert_request` and {func}`protoverta.convert_response` take a decoded JSON body, its protocol and the target protocol (`"chat"`, `"messages"` or `"responses"`, or a {class}`~protoverta.Protocol`). They return a new body; the input is not modified.

```python
from protoverta import ConversionError, convert_request

try:
    responses_request = convert_request(chat_request, "chat", "responses")
except ConversionError as error:
    print("not portable:", error)
```

Bare conversion omits tool and choice metadata that only the original request knows. To restore it on Responses output, bind the original request with {func}`protoverta.bind_response_context`. The gateway does this for you.

## Streams

{class}`protoverta.StreamTranscoder` converts a server-sent-events stream from one protocol to another. Feed it the raw upstream bytes as they arrive and call `finish()` at the end of the stream:

```python
from protoverta import StreamError, StreamTranscoder

transcoder = StreamTranscoder("chat", "responses")
try:
    for chunk in upstream_chunks:          # bytes, split anywhere
        client.write(transcoder.feed(chunk))
    client.write(transcoder.finish())
except StreamError as error:
    ...  # invalid, interrupted or unsupported stream
```

Translating Chat Completions or Responses into Messages needs `messages_usage_policy="deferred"`: Messages announces input usage in its first event, which those protocols only report at the end. Initial counts are then provisional and corrected in `message_delta`.

The lower-level {class}`~protoverta.StreamDecoder` turns events into portable {class}`~protoverta.Delta` items and {class}`~protoverta.StreamEncoder` turns deltas into events.

## Capabilities, catalogs and token counts

- {class}`~protoverta.CapabilityRegistry` records which features an exact provider model supports and blocks unsupported requests before they are sent. See [capabilities](compatibility/capabilities.md).
- {func}`~protoverta.fetch_catalog` reads a provider's model list with bounded paging, sizes and redirects refused. See [catalogs](compatibility/catalogs.md).
- {func}`~protoverta.count_input` and {func}`~protoverta.assess_context` count input tokens (natively or as a labelled estimate) and check them against a context window. See [context preflight](compatibility/context-preflight.md).

The complete reference is in [Python API](api/index.md).
