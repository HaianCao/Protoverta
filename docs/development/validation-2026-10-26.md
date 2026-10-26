# Local validation, 2026-10-26

## Result and scope

The declared text/function-tool conversion subset passes the source and installed
wheel suites and the local CLI/browser/installer experiments below. This is not
full protocol compliance or certification of every model/provider. The full
OpenResponses schema audit remains red and is reported separately.

Environment: Windows, Python 3.13.9, Codex CLI 0.164.0, Claude Code 2.1.308.
Core: 0.3.0a7; consumers: 0.2.0a1 on their `integration/protoverta` branches.
These version numbers identify local snapshots, not a new published release.
No real provider keys were read, no live provider requests were made, and the
real installations, PATH, configurations and sessions were not modified.

Committed implementation inputs:

- Protoverta `6bed81e`: tool contracts and finishing-chunk stream usage.
- CodexApter `eaad7b8`, then whitespace cleanup `c8ab2cf`.
- ClaudApter `4359bc8`, then whitespace cleanup `10f6dc9`.

This review also fixes the diagnostic transport wrapper to forward the gateway's
`observer` parameter and adds two loopback regression cases. Two additional tests
cover the resume probe's explicit initial reasoning policy. The portable-history
probe now disables Codex reasoning summaries before the initial persistent turn.
Runtime protocol conversion policy was not relaxed.

## Rerun evidence

| Gate | Current result | Meaning |
| --- | --- | --- |
| Core source pytest | 1324 passed, 214.16 s | Includes the four added harness regression cases |
| Core installed-wheel pytest | 1324 passed, 207.36 s | Checkout Python path disabled; runtime imported from site-packages |
| CodexApter source / installed pytest | 181 passed + 12 subtests / same | Source 25.97 s; installed 23.42 s |
| ClaudApter source / installed pytest | 148 passed + 9 subtests / same | Source 23.71 s; installed 21.24 s |
| SDK source / installed | 19 passed / 19 passed | Five positive accumulation routes, six-route truncation checks, and two explicit negative Messages-to-Responses usage-schema cases |
| Property source / installed | 24 passed / 24 passed | 50 deterministic generated examples per property; bounded, not exhaustive |
| Scoped mypy | Pass | Six designated runtime modules and positive/negative typing fixtures; not whole-codebase strict typing |
| Ruff | Pass, all three repos | Final core changes checked again |
| Packaging | Six artifacts pass Twine | All three wheels and sdists build; every packaged runtime `.py`/`.html` byte matches source |
| Real CLI, installed consumers | Six passes | Each of Codex and Claude against Chat, Messages and Responses loopback upstreams, including two read-only tool calls and the final answer |
| Browser setup | Both pass | Real Chromium via Playwright 1.64.1, isolated profiles and local requests only; launch callback stubbed |
| Windows scripts | Both pass | Install, repeat-install/update, uninstall; synthetic configuration preserved; no persistent PATH changes |
| Bounded load/cancellation | Pass | 300 measured requests plus 183 warmup requests; opened responses closed, no retained lifetime threads, handles or TCP growth |
| Official OpenResponses audit | **0 pass / 12 fail / 0 skip** | Full required response schema is not implemented; this is not converted into an accepted pass |

The initial optional-gate launch used an incorrect Python module name and a tool
environment without the pinned SDK. Corrected launches used the declared scripts
and pinned SDK environment; both source/installed gates then passed. The default
Playwright browser binary was absent. Chromium was downloaded into the temporary
validation directory and both browser checks reran successfully. These setup
failures are not hidden product passes.

The load test stopped at its request cap, before the 35-second ceiling. It uses
four workers but its fast fixture reached only one simultaneously active upstream
callback. It is bounded cleanup evidence, not a sustained-load benchmark or proof
of long-duration memory stability. Concurrent gateway/session isolation is also
covered separately by pytest.

## Coverage audit

Measured with coverage.py 7.17.0, branch collection enabled, against each project's
ordinary source pytest suite:

| Package | Lines/statements | Branches |
| --- | --- | --- |
| Protoverta | 90.6% (4936 / 5449) | 82.4% (2191 / 2660) |
| CodexApter | 81.2% (1247 / 1536) | 70.9% (360 / 508) |
| ClaudApter | 78.3% (933 / 1192) | 68.0% (257 / 378) |

These are separate statement/branch fractions, not the combined percentage
coverage.py also displays. Browser, SDK, generated, installed and real-CLI runs
were not merged into these figures. Child-process execution is not collected:
in particular ClaudApter's CLI module shows zero in this in-process measurement,
despite being exercised by the installed CLI smoke runs. That does not demonstrate
coverage of every CLI routing/error branch.

The ordinary suites exercise all six translated directions, native forwarding,
text and tool histories, Unicode/chunk boundaries, sparse/parallel tool streams,
call-ID matching, finite JSON arguments, measured usage, terminal lifecycle,
truncation, strict reasoning refusal, HTTP authentication/framing/limits,
timeouts/disconnects, session isolation, local setup/config writes, catalog/cache
failures and Windows command/shim boundaries.

Not every possible case is covered. Large missing-line groups remain in stream
decoding (81 lines), HTTP server handling (71), conversion core (50), stream
pipeline (36), session diagnostics (30), and client tool search (28). Consumer
web/config/CLI/doctor error paths have additional gaps. Some are defensive or
platform branches; none is assumed unreachable merely because tests miss it.
Coverage counts neither the set of all valid protocol inputs nor every provider's
extensions. Python versions other than 3.13 and Linux were not rerun here; remote
CI was not triggered by this local validation.

## What converts today

| From / to | Chat Completions | Messages | Responses |
| --- | --- | --- | --- |
| Chat Completions | Native forwarding | JSON + SSE subset* | JSON + SSE subset |
| Messages | JSON + SSE subset | Native forwarding | JSON + SSE subset** |
| Responses | JSON + SSE subset | JSON + SSE subset* | Native forwarding |

* Streaming into Messages from another protocol requires explicit deferred usage
policy (`messages_usage_policy="deferred"`): initial counts are provisional, final
counts must be measured. The strict default rejects a route where the required
initial usage is unavailable. The launcher integration explicitly selects its
supported policy; standalone callers must do so themselves.

** Messages lacks a reasoning-token usage subtotal. Conversion remains available,
but the pinned OpenAI SDK's strict Responses terminal validation rejects the
missing `output_tokens_details` fact. Unknown cache/reasoning subtotals from any
upstream are not invented as zero. SDK compatibility depends on reported data.

The shared subset includes ordered text, system/user/assistant history where
placement is representable, function definitions/calls/results with stable IDs,
forced function selection, measured usage, termination and incremental SSE.
Additional bounded support includes ordinary-user URL/base64 images and inline
PDF inputs on six request routes, supported strict JSON schema formats, `top_p`,
and route-appropriate temperature. These are supported shapes, not evidence that
an arbitrary model can process the content.

Some features have narrower routes: Chat/Responses completed-JSON custom tools;
Messages/Responses image-bearing tool results; Chat/Messages stop controls;
Chat/Responses strict function flags and JSON-object output. See the individual
[compatibility documents](../compatibility) before relying on those extensions.

Unsigned public `reasoning_content` and Responses `reasoning_text` can map between
Chat and Responses only with explicit public-reasoning opt-in and provider support.
Signed/hidden/encrypted reasoning is not downgraded to ordinary text. Hosted tools,
audio/video, arbitrary vendor fields and provider-owned file/state IDs are not
universally cross-converted. Native forwarding preserves valid native payloads
without proving that the provider supports every feature.

The official schema audit uses the unmodified runner pinned to
`8907bc3b7c4300ff5c21fd647cfde9929243afa1`. All twelve selected checks still fail
required snapshot/usage fields. Missing provider facts are not guessed just to
satisfy a validator. See [the audit and reproduction](openresponses-audit.md).

## Resume observations from this run

- Codex Chat -> Chat, changing provider ID and model after a completed read-only
  tool loop: pass, including shape-only tracing.
- Codex Responses -> Chat, changing provider ID and model after a completed tool
  loop: pass when the initial session explicitly disabled reasoning summaries.
  Same session ID, previous assistant answer and matching tool history reached
  the newly selected upstream/model.
- Claude Messages -> Responses with changed provider ID/model and completed
  read-only tool history: pass.
- The first Codex Responses -> Chat attempt with native initial summary defaults
  was rejected before dispatch: the resumed client requested a nonportable
  reasoning summary. That refusal remains intentional. The diagnostic wrapper's
  separate observer bug was fixed before the successful traced reruns.

This tests synthetic provider configurations and fixture models, not real vendor
identity/resource migration. Other boundaries in [resume compatibility](../compatibility/resume.md)
remain, especially Messages placement of later developer instructions and opaque
server-owned state. Existing sessions are not rewritten. Azure resource-bound
items cannot be transferred by changing a URL or re-encoding their ID.

## Remaining work and acceptance boundary

1. Resolve the full Responses snapshot/provenance and strict usage-schema gaps
   without fabricating provider measurements or silently weakening strict policy.
2. Expand meaningful tests of uncovered CLI/web/doctor and defensive stream/HTTP
   branches; add child-process coverage if an aggregate metric is required.
3. Run the isolated acceptance procedure on the intended installation's CLI
   versions, including manual setup-to-interactive-terminal handoff. Browser QA
   stubs launch; real CLI checks run separately, so their combination is not yet
   an automated interactive-terminal proof.
4. Run a small authorized live matrix for the exact providers/models being used,
   then longer resource/cancellation loads. No live evidence was added here.
5. Run remote Windows/Linux and Python-version CI before publication. Keep the
   launcher integrations separate from stable `main` until explicitly approved.

The step-by-step [local preview guide](local-preview.md) builds and installs all
three wheels into a disposable venv, tests loopback first, and separates any
optional live provider checks from keyless acceptance.
