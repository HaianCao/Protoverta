# Local Codex client acceptance checks

Checked with Codex CLI **0.164.0** on Windows, using scripted loopback upstreams
and disposable client state. No live provider requests or user-session changes.
This is client/protocol evidence, not model/provider certification.

## Process lifetime versus captured pipes

The earlier persistent `get_goal` smoke timeout did not prove that the Codex
parent process hung. Timeout instrumentation observed parent exit code 0 while
descendant processes retained captured stdout/stderr. A persistent text-only
native Responses control reproduced this, independently of conversion/tools.
Own-job diagnostics identified lingering git.exe/git-remote-https.exe processes
(and conhost.exe), not a running Codex parent. Disabling daemon auto-start alone
was insufficient: repeated installed-wheel tests still intermittently failed.

The fixture runner now distinguishes a running parent from an exited parent
with open pipes. On Windows it can report only image basenames of processes in
its own containment job, never global process lists, command lines or secrets.
Timeouts remain failures and cleanup is restricted to that job/process group.

Fixture invocations disable daemon auto-start and pass `--no-daemon` at Codex
root scope. They also disable plugins/apps/remote-plugin discovery and startup
updates, and restrict Git to local file transport (`GIT_ALLOW_PROTOCOL=file`,
`GIT_TERMINAL_PROMPT=0`) in the temporary child environment. This prevents
unrelated startup Git downloads/credential prompts from contaminating a local
protocol fixture. The exact Codex startup component launching Git was not
established by image names alone. They keep persistent temporary threads for goal tools, the read-only
sandbox, and normal tool error checks. They do not turn an ephemeral goal-tool
failure into success or force these daemon settings on launcher users.
With this offline startup policy, the installed-wheel Chat `get_goal` loop
passed three consecutive checks with two results and exit code 0. Native
Responses and source Chat/Messages controls also passed. This resolves the
local acceptance fixture; it is not a claim to have fixed Codex's background
process behavior in every production configuration.

## Real client tool search

`scripts/smoke_tool_search.py` starts one local stdio MCP fixture exposing only
`fixture_lookup`. This tool returns a fixed synthetic value without I/O or side
effects; it has read-only annotations. No global MCP registration is made.

The test uses a temporary model catalog derived from bundled metadata to enable
the search capability while disabling unrelated verbosity/custom/code-mode
features. Do NOT copy this artificial catalog into production configuration or
infer real provider capabilities from it.

Three model rounds are required:

1. Codex advertises client-executed search; the scripted model requests it.
2. Codex returns matched `tool_search_output` containing the MCP namespace.
   The translated route exposes its loaded function through reversible aliases.
3. Codex executes the MCP function and returns the exact synthetic result before
   the final answer. A successful MCP completion event and exit code 0 are
   required; a marker/tool-result count alone is insufficient.

The native Responses control, bare Chat gateway, source CodexApter Chat/Messages
gateways and isolated installed-wheel Chat/Messages gateways passed this flow.
Both translated paths use explicit `client_tool_search="function"` and namespace
flattening. They retain the documented eager-schema/cache-placement differences.

Run from the Protoverta checkout with matching local packages installed:

```powershell
python scripts/smoke_tool_search.py --upstream responses
python scripts/smoke_tool_search.py --upstream chat --adapter-source ../free-codex-cli
python scripts/smoke_tool_search.py --upstream messages --installed-adapter
```

The normal model harness uses text/read-only fixtures too:

```powershell
python scripts/smoke_clients.py --client codex --upstream chat --tool-loop --installed-adapter
```

The fixtures must fail closed if a later client changes its required schema,
tool-search lifecycle or tool permissions. Provider validation, remote CI and
release/version changes are separate gates.

Official shape reference: [client-executed tool search](https://developers.openai.com/api/docs/guides/tools-tool-search).
