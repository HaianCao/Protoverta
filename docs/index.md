# Protoverta

Protoverta converts requests, responses and streams between **OpenAI Chat Completions**, **Anthropic Messages** and **OpenAI Responses**. Use it as a Python library or run it as a local HTTP gateway in front of any provider.

It is strict by design: a field whose meaning cannot be carried to the other protocol is rejected with a clear error, never dropped or invented. Reasoning state, token counts and server-side history are never fabricated.

| | Chat Completions | Anthropic Messages | Responses |
|---|---|---|---|
| **From Chat Completions** | native | translated | translated |
| **From Anthropic Messages** | translated | native | translated |
| **From Responses** | translated | translated | native |

All nine routes cover text, user images and PDF documents, client-side function tools (including streaming and parallel calls), forced tool choice and JSON output formats. Native routes forward payloads unchanged while validating stream framing and lifecycle.

**Using Codex CLI or Claude Code with another provider?** You do not need to configure the gateway yourself: install [CodexApter](https://github.com/HaianCao/CodexApter) (`codex-ca`) or [ClaudApter](https://github.com/HaianCao/ClaudApter) (`claude-ca`), which run Protoverta for you.

```{toctree}
:maxdepth: 2
:caption: Using Protoverta

getting-started
library
gateway
http-api
retries
configuration
```

```{toctree}
:maxdepth: 1
:caption: Reference

cli
api/index
compatibility/index
```

```{toctree}
:maxdepth: 1
:caption: Project

changelog
security
contributing
development/index
```
