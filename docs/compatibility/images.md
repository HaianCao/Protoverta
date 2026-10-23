# User image input

The converter supports ordered text and image blocks in ordinary **user**
messages across all six translated request routes. Both completed JSON and
streaming requests use the same input converter. Output remains text/tools.

| Source | Portable representation |
| --- | --- |
| Chat `image_url.url` | HTTP(S) URL or base64 data URL |
| Responses `input_image.image_url` | HTTP(S) URL or base64 data URL |
| Messages `image.source` | URL or `{type: "base64", media_type, data}` |

PNG, JPEG, WebP and GIF media types are accepted. Base64 syntax is validated;
the gateway does not decode image dimensions, determine whether a GIF is
animated, or promise that the target model accepts the bytes. Provider size,
dimension, animation and vision-capability restrictions still apply. The HTTP
gateway's request-body limit also applies to base64 input.

Image references and their order are retained exactly. The gateway never
fetches URLs, reads local paths, creates Files API uploads, or transcodes bytes.
HTTP(S) URLs may retain query strings (for example signed URLs); credentials,
fragments and whitespace are rejected. Use trusted image hosts: the selected
upstream provider, not Protoverta, retrieves these references.

Chat ↔ Responses preserves `detail` (`auto`, `low`, `high`, `original`).
Messages has no equivalent selector: omitted/`auto` uses its default image
handling; explicit `low`, `high` and `original` are rejected rather than dropped.
This mapping does not guarantee identical resolution or token cost.

Provider-owned `file_id`, assistant/generated images, image tool results,
PDF/audio/video and unknown image options remain errors on translated routes.
Native requests are forwarded unchanged, subject to gateway transport limits.
Capability records can mark `images` false or require explicit verified support;
a `/models` listing alone is not evidence that vision works.

One live SSE sample passed Responses → Chat through NVIDIA NIM
`minimaxai/minimax-m3.1-flash`, with public reasoning enabled. The probe generated
a 32×32 red-left/blue-right PNG and required both colors in the correct order in
the answer. It did not send a user image. This verifies that sample/route, not
all image formats/models or all six live routes. Reproduce with
`scripts/probe_images.py --allow-live` and explicit source/upstream/model/key
environment options.

Sources: [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision)
and [Anthropic vision](https://platform.claude.com/docs/en/build-with-claude/vision).
