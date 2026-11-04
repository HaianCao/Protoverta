# Independent stream conformance

The normal core suite includes reproducibly randomized byte partitions and
semantic round-trips across all six translated directions. Fixtures in
`tests/wire_cases.py` are hand-authored, not produced by Protoverta encoders.
They include UTF-8 split boundaries, sparse parallel Chat tool indices and tool
arguments carried in the same chunk that opens the call. Terminal truncation
must never become successful target completion. Generated IDs/timestamps and
target-specific block numbering are not expected to be byte-identical.

## Official SDK consumer gate

In an isolated development environment:

```text
python -m pip install -e ".[test,sdk-test]"
python scripts/check_sdk.py
```

Pinned parsers: OpenAI Python 3.37.0 and Anthropic Python 2.3.0. These are test-only
dependencies, not part of the runtime core. Missing/wrong versions and skipped/
empty SDK suites fail. The core pytest suite does not import SDK tests, so basic
contributors can still run it without provider SDK dependencies.

The gate exercises all six translated directions through real loopback HTTP
listeners with strict SDK response validation and retries disabled. Five routes
have positive accumulation checks. Positive Responses fixtures explicitly send
tools, tool choice and parallel-call permission. Missing request policy is not
filled with guesses: two additional strict SDK cases assert the missing-field
failure at response.created. Messages → Responses has explicit negative
schema checks for unknown reasoning usage, not a positive SDK compatibility claim.
Provider transport is an injected fixture callback: no request reaches a vendor,
no real key is needed, proxies are disabled on the SDK client, and fixture model
IDs are not real provider capability records. It verifies text, tool arguments,
call IDs/order, measured usage, length termination and truncated-stream errors.
Messages and Responses accumulation helpers are exercised; Chat uses the SDK's
typed chunk parser and a test-side accumulator. No tools are actually executed.

Keyless test processes use an allowlisted OS environment, excluding inherited
provider keys/auth settings, proxies, telemetry exporters and pytest flags.
Core/SDK pytest hooks scrub their own child-process environment too, so a test
assertion cannot accidentally print inherited credentials in argument locals.
Synthetic variables may be set explicitly by individual tests. The coordinated
validator and SDK/type gates do not change the launching shell or persistent
environment. Never use this keyless environment helper in a live probe that
needs an explicitly authorized credential. Tracebacks are short and captured
output is hidden in the coordinated gate; these are defense in depth, not
permission to log real credentials elsewhere.

`check_sdk.py --installed` disables checkout pythonpath and tests the installed
core. The coordinated ecosystem validator installs matching wheels plus the
pinned test SDKs into a fresh venv and runs this gate. Source core CI has a
separate Windows/Linux x Python 3.11/3.13 SDK job; release also checks it. These
workflow definitions are not evidence that remote CI has run.

## Explicit compatibility findings

- Missing cached/reasoning-token subtotals remain unknown and are omitted from
  translated Responses usage. Measured zero is preserved; it is never inferred
  from absent reasoning text. Messages has no reasoning-token subtotal, so its
  translated Responses terminal snapshots fail the pinned OpenAI SDK's strict
  `output_tokens_details` requirement. Two SDK tests assert that exact schema
  failure (completed and incomplete), without disabling strict validation,
  skipping tests or fabricating zero. The 21-test suite therefore includes
  negative compatibility evidence; it does not certify six successful SDK routes.
  Protocol-level text/tool conversion remains available, with this documented
  schema limitation. A full-schema consumer needs provider-reported measurements
  or an explicitly designed policy for unavailable usage, not guessed facts.
- Function arguments must contain a JSON object with finite numbers at every
  depth. NaN, Infinity, and float overflow such as `1e309` are rejected in JSON
  and SSE conversion. Valid argument strings retain their original spelling.
  Invalid translated requests fail locally without upstream dispatch/retry.
- Anthropic's SDK appends `/v1/messages`; the SDK fixture uses a root base URL.
  This is SDK URL construction, not a reason to rewrite operator API prefixes.
- Translated Chat requests accept `stream_options.include_usage=true` while
  streaming. Messages/Responses have native usage events, so that transport
  request does not become a model parameter. Explicit false/nonboolean values
  and unknown options are rejected rather than silently ignored. Native Chat
  remains unchanged.
- The current OpenAI SDK strict usage schema requires `cache_write_tokens`.
  Protoverta now preserves MEASURED cache writes between Chat/Responses details
  and Messages `cache_creation_input_tokens`, without double-counting total
  input. If upstream omitted cache-write accounting, output still omits it;
  it is NOT invented as zero merely to satisfy the latest strict SDK. Therefore
  this gate's positive fixtures explicitly provide measured counts, including
  measured zero. Older providers with missing detail are not thereby certified
  compatible with every strict SDK version. This does not add caching itself.
- The pinned Responses SDK's `get_final_response()` helper requires
  `response.completed`; it raises for a valid `response.incomplete` event. The
  gate verifies that exception and inspects the SDK-validated incomplete terminal
  directly. The gateway must NOT emit completed to appease the helper.

These local tests are independent-client evidence for the declared subset, not
full schema validation, universal model compatibility or live certification.
See [generated Responses metadata](../compatibility/response-metadata.md) for
the standalone request-context API and the alpha metadata contract correction.
Property tests are a separate pinned gate:
`pip install -e ".[test,fuzz-test]"` then `python scripts/check_fuzz.py`.
It runs 24 six-direction properties with 50 generated examples each, covering
Unicode/JSON/byte partitions, length termination, absent terminals and invalid
tool JSON. Hypothesis shrinking is enabled; the fixed deterministic profile
disables the example database and timing deadline to keep runs reproducible.
This is bounded generated coverage, not exhaustive verification. `--installed`
tests the installed library. Official full-schema findings are tracked separately
in [the OpenResponses audit](openresponses-audit.md); they are not claimed as passes.

Browser QA and measured soaks now have opt-in scripts, documented in
[the validation matrix](validation-matrix.md). Additional SDK/client versions,
provider certification and longer production loads remain separate coverage.
Never send real session payloads as fixtures.

References: [OpenAI Python SDK](https://developers.openai.com/api/reference/python),
[OpenAI cache accounting](https://developers.openai.com/api/docs/guides/prompt-caching),
[Anthropic streaming helpers](https://github.com/anthropics/anthropic-sdk-python/blob/main/helpers.md).

## Persistent evidence and SDK candidates

`check_sdk.py --installed --report-dir NEW_DIRECTORY` requires all 29 mandatory
cases and retains `report.xml` plus `sdk-evidence.json`. The directory must be
new: an older success artifact must not survive and impersonate this run. Missing
or mismatched baseline dependencies retain a FAILED version record; an upstream
test failure never becomes PASS through report parsing.

The separate `SDK candidate evaluation` workflow upgrades stable SDKs on a
disposable runner and invokes `--allow-candidate-sdk`. The regular CI and release
gate keep their exact SDK pins. Candidate artifacts record actual/baseline
versions, `sdk_version_drift`, verdict and workflow stage outcomes. A pass with
no drift is baseline evidence, not certification of a newer version. Dependency
installation failures remain failures, not semantic incompatibility verdicts.
See [the CI job review](ci-jobs-review-2026-11-04.md) for local evidence and
remote/candidate gates still pending.
