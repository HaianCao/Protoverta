# Explicit generation-control follow-up

This checkpoint predates enabled text-logprob translation. See the
[subsequent implementation](text-logprobs-2026-11-04.md) for current positive
Chat/Responses support; the earlier validation receipt remains historical.

The prior reported-tier fix was committed as `1d2113b`, without push. This
follow-up implements bounded controls without inventing response defaults.

## Implementation

The canonical Request now carries frequency/presence penalties and an explicit
disabled-logprob marker. Penalties from the incoming OpenResponses dialect map
to a Chat target with finite `[-2, 2]` validation. Targets without an enabled
equivalent reject even explicit zero; no outgoing Responses extension is guessed.
Capability feature detection includes both penalties and recognizes zero as an
explicit request, so negative model evidence blocks I/O.

Explicitly disabled logprobs map across the supported Chat/Responses inputs:
Chat sends `logprobs=false`, Responses sends `top_logprobs=0`, and Messages has
no probability-output field. Strict output decoders continue rejecting actual
logprob data. Enabling selected-token probabilities, including true with zero
top-k, remains unsupported. Positive top-k and invalid types/ranges reject.

Generated Responses JSON/SSE metadata projects explicit penalties and disabled
top-k from the validated original request, matching real outgoing controls.
Unknown values remain absent. Native paths, strict reasoning and fallback/retry
policy are unchanged. No setup UI decision is added.

See [route matrix and references](../compatibility/generation-controls.md).

## Validation

83 new tests cover values/boundaries, malformed and nonfinite inputs, copy
isolation, supported and rejected directions, enabled/disabled probability
distinctions, Messages input rejection, actual HTTP wire controls, JSON/SSE
snapshot agreement and explicitly negative model capabilities. A focused run
including existing response-policy tests passed 121 cases. Full-core and
installed-wheel results are recorded in
[validation receipt](generation-controls-validation-2026-11-04.json).

All provider traffic is synthetic loopback. No real installation, configuration,
session, PATH, key or provider was changed. Prior launcher source-suite results
apply only to their unchanged source; current CLI smoke checks exercise the
new matched core wheel. Full-schema acceptance remains a separate failing gate
when required source/default facts are absent; these controls must not be
defaulted to zero just to satisfy the audit.
