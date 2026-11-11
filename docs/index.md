# Protoverta

::::{container} pv-hero

```{image} _static/protoverta_logo.png
:alt: Protoverta logo
:class: pv-hero-logo
:width: 112px
```

```{container} pv-title
Protoverta
```

```{container} pv-tagline
One request, any provider. Protoverta converts requests, responses and streams between **OpenAI Chat Completions**, **Anthropic Messages** and **OpenAI Responses**, as a Python library or a local gateway.
```

:::{container} pv-actions

```{button-ref} getting-started
:color: primary
:ref-type: doc

Get started
```

```{button-ref} http-api
:color: secondary
:outline:
:ref-type: doc

Gateway HTTP API
```

```{button-link} https://github.com/HaianCao/Protoverta
:color: secondary
:outline:

GitHub
```

:::

:::{container} pv-install

```console
pip install protoverta
```

:::

::::

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} {octicon}`arrow-switch` Nine routes
:class-card: pv-card
:link: library
:link-type: doc

Every direction between the three APIs, for JSON and streaming: text, images, PDFs, function tools and output formats.
:::

:::{grid-item-card} {octicon}`shield-check` Strict, never invented
:class-card: pv-card
:link: compatibility/index
:link-type: doc

A field that cannot keep its meaning is rejected with a clear error. Reasoning, token counts and history are never fabricated.
:::

:::{grid-item-card} {octicon}`server` Local gateway
:class-card: pv-card
:link: gateway
:link-type: doc

One command puts any provider behind the endpoint your client already speaks, on `127.0.0.1` only.
:::

:::{grid-item-card} {octicon}`sync` Smart retries
:class-card: pv-card
:link: retries
:link-type: doc

Transient provider errors are retried by class; deterministic ones are reported at once. Output is never sent twice.
:::

:::{grid-item-card} {octicon}`pulse` Streams you can trust
:class-card: pv-card
:link: compatibility/runtime
:link-type: doc

Framing and lifecycle are validated, keep-alives prevent idle timeouts and deadlines bound every request.
:::

:::{grid-item-card} {octicon}`package` Zero dependencies
:class-card: pv-card
:link: api/index
:link-type: doc

Pure Python 3.11+, fully typed public API, attested releases with SHA-256 checksums.
:::

::::

## Routes

:::{container} pv-matrix

| From ↓ · To → | Chat Completions | Anthropic Messages | Responses |
|---|---|---|---|
| **Chat Completions** | {bdg-primary}`native` | {bdg-secondary}`translated` | {bdg-secondary}`translated` |
| **Anthropic Messages** | {bdg-secondary}`translated` | {bdg-primary}`native` | {bdg-secondary}`translated` |
| **Responses** | {bdg-secondary}`translated` | {bdg-secondary}`translated` | {bdg-primary}`native` |

:::

Native routes forward payloads unchanged while validating stream framing and lifecycle. Translated routes cover the portable subset; see the [compatibility guides](compatibility/index.md) for each feature's exact contract.

## Using Codex CLI or Claude Code?

You do not need to configure the gateway yourself. The launchers install and run Protoverta for you:

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} CodexApter
:class-card: pv-card
:link: https://github.com/HaianCao/CodexApter

OpenAI Codex CLI with Responses, Chat Completions or Messages providers. Command: `codex-ca`.
:::

:::{grid-item-card} ClaudApter
:class-card: pv-card
:link: https://github.com/HaianCao/ClaudApter

Claude Code with Messages, Chat Completions or Responses providers. Command: `claude-ca`.
:::

::::

```{toctree}
:hidden:
:caption: Using Protoverta

getting-started
library
gateway
http-api
retries
configuration
```

```{toctree}
:hidden:
:caption: Reference

cli
api/index
compatibility/index
```

```{toctree}
:hidden:
:caption: Project

changelog
security
contributing
development/index
```
