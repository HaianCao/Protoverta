# Authentication, setup boundaries and doctor regression fixes

These fixes follow the earlier validation snapshot from the same date. Historical
JSON snapshots/results remain tied to their recorded runtime hashes; they are not
silently relabelled as evidence for this changed source tree.

- Non-ASCII inbound bearer tokens now return 401; configured gateway tokens must
  be ASCII. Both setup apps safely return 403 for malformed query/header tokens.
  Valid-token requests still work after a rejected request.
- Both launcher setup servers now use independent standard-library-only HTTP
  helpers: at most 16 accepted handlers, absolute 10-second header /15-second body
  deadlines within a 30-second input budget, bounded writes and pending-client
  socket shutdown on close. Direct setup imports work with Protoverta explicitly
  blocked. See [request boundaries](../compatibility/request-boundaries.md).
- Doctor JSON and SSE decoders respect the explicit public-reasoning policy.
  Unsigned Chat/Responses reasoning no longer causes a false text/stream failure
  when enabled. Disabled policy and opaque/encrypted state still fail. Passing
  text checks does not certify reasoning capability.

Verified on Windows/Python 3.13.9 against matched local wheels:

| Check | Outcome |
| --- | --- |
| Protoverta complete installed suite | 1071 passed, no skips |
| CodexApter complete installed suite | 144 passed +12 subtests, no skips |
| ClaudApter complete installed suite | 111 passed +9 subtests, no skips |
| Pinned independent SDK gate | 19 passed |
| Real Chromium setup checks | Both source and installed launchers passed, actual bounded server factory |
| Ruff, scoped typing and whitespace | Passed |
| Wheel/sdist validation | All three projects built; six archives passed Twine; runtime Python/HTML matched wheel bytes |

Tests use synthetic keys/data and temporary configuration. No provider inference,
actual user installation/PATH/config/session change, commit or publication occurred.
The previous measured soak and real CLI matrix were not rerun for this narrow
batch; their original scope/results remain in the historical record. The known
[full OpenResponses schema gap](openresponses-audit.md) is not solved by these fixes.
