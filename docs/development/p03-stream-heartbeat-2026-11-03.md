# P03 — Post-payload streaming keepalives

Date: 2026-11-03. Status: COMPLETE for the documented P03 contract; stop for user review
before committing or starting P04. No P03 commit, push, merge, release or live-provider request.

## Baseline and scope

P02 was committed before this work: Protoverta `16d194c`, CodexApter
`5c06c60`, ClaudApter `9ca4822`. Only Protoverta changes in P03. Consumer
integration branches remain separate from their production main branches.

Source runs use an isolated editable preview (Python 3.13.9, pytest 9.2.0,
Ruff 0.17.2), not an installed-wheel acceptance claim. Real client versions:
Codex 0.166.0, Claude Code 2.1.308. Pinned SDKs: OpenAI 3.37.0 and Anthropic
2.3.0. Child CLI state, home and workspace are temporary; child environments
are allowlisted. No real configuration, session, installation or PATH was changed.

## Reproduction and decision

The six-route loopback reproduction sent valid content, held upstream open and
then required another downstream frame. Before the fix it timed out reading
that frame. This demonstrates a missing transport keepalive, not a claim that
a real provider or client has already suffered a particular production timeout.

Translated streams now send a keepalive after 15 seconds without a downstream
write, once their first supported payload has been delivered. Messages uses
`event: ping` / `{"type":"ping"}`. Chat and Responses use an SSE comment.
Native streams are unchanged, including byte preservation.

Before the first supported payload, metadata stays buffered. Unsupported first
content still returns non-retryable HTTP 422. There is deliberately no startup
heartbeat; initial silence remains bounded by the existing request deadlines.
No new user-facing setting is necessary.

One serialized writer protects full-frame writes and socket timeout changes.
The timer never reads upstream, updates model progress/first-payload tracing,
invents usage/text or extends deadlines. EOF/error stops it before terminal
output. A failed downstream write cancels registered upstream sockets. Each
active translated post-payload stream has at most one monitor. Timer writes
have a one-second timeout, with a 1.25-second shutdown join. Arbitrary injected
callbacks still have to honor their timeout/cancellation contract.

## Validation evidence

| Check | Actual result |
|---|---|
| New writer and real-socket tests | 28 passed |
| Pinned official SDK gate | 26 passed, 25.21 seconds (final SDK rerun) |
| Short real Codex / Claude parser checks | Both passed; each observed 8 keepalives, one upstream request |
| Real Codex / Claude, 16-second EOF pause and 15-second heartbeat interval | Both passed; each observed 1 keepalive, one upstream request |
| First complete core run | 1 failed, 1742 passed; fixture issue described below |
| Final complete core rerun | 1743 passed; intermediate fixture assertion correction described below |
| Ruff | Passed on final code |
| Measured loopback soak | Passed; 120.283 seconds of workload, 222 requests issued |

Socket tests cover six translated directions, native controls, buffered tool
arguments, terminal pending verified EOF, invalid trailing bytes, unsupported
first reasoning, disconnect during silence, and total/progress deadlines.
Writer tests exercise serialization, failed/timeout writes, no native/startup
monitor, deadline classification and oversized/invalid intervals. Existing
stream-size/framing/lifetime regression cases remain part of the full suite.

The old search-client fixture used a temporary fake adapter root, then tried
to load the real harness policy from that nonexistent checkout. Its fix stubs
the independently tested policy resolver and asserts both its input and the
forwarded override in bare-client mode. An intermediate test correction incorrectly expected that override in wrapper arguments too; wrappers apply it within the launcher. The corrected file passed 10/10 before the final full-suite rerun. No runtime fallback or weakened policy was added.

The soak observed 201 upstream opens and 201 closes, no active upstream after
completion, zero lifetime/heartbeat monitors retained, and no client failures.
It included 21 client cancellations and 19 correctly classified invalid streams.
Resource policy passed: retained threads -1, handles +2, TCP +0; peak RSS
41,549,824 bytes. This short-run resource observation does not certify days of
operation. The fast soak streams primarily exercise monitor churn; actual
heartbeat emission is separately demonstrated by socket, SDK and CLI tests.

## Reproduction commands

Run with an isolated preview Python, from the Protoverta checkout:

```powershell
& $PreviewPython -m pytest -p no:cacheprovider
& $PreviewPython scripts/check_sdk.py
& $PreviewPython scripts/smoke_heartbeat.py --client codex --pause-seconds 16 --heartbeat-interval 15
& $PreviewPython scripts/smoke_heartbeat.py --client claude --pause-seconds 16 --heartbeat-interval 15
& $PreviewPython scripts/soak_local.py --sustained --seconds 120 --requests 1000 --concurrency 2 --interval 1 --measure-resources
```

The SDK check requires the pinned SDK test dependencies; measured soak requires
the soak-test extra. All these checks use synthetic local upstreams. They do
not require provider credentials. CLI parser checks use bare real clients,
not wrapper or installed-wheel certification.

## Limits and next item

- Keepalives do not cover pre-payload silence or make an incompatible route valid.
- They cannot make unsupported reasoning equivalent or bypass a dead progress deadline.
- A blocked downstream data write owns the writer until its existing bounded
  timeout; the timer cannot interleave bytes to pretend it is making progress.
- The strict SDK Messages-to-Responses unknown-usage rejection remains unchanged.
  The five-route positive SDK subset is not six-route full-schema certification.
- Existing full-schema audit and live Internet search gates remain unresolved;
  this transport change does not alter their verdicts.
- P04 will test actual parent/subagent inference and lifecycle. P03 does not
  enable cross-protocol agents or certify auxiliary-model routing.

The [SSE standard](https://html.spec.whatwg.org/multipage/server-sent-events.html)
defines comment lines as non-events and discusses keepalive comments.
[Claude's gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol)
documents streaming pings and required terminal events. Local parser and
socket evidence above, rather than documentation alone, supports this subset.
