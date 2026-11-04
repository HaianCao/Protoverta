# Reported serving tier

Translated Chat and Responses output preserves an upstream-reported
`service_tier`. Missing/null observations stay absent. Protoverta never fills
this field from a request, operator profile, model catalog, or an assumed
`default` tier. It does not select or buy a processing mode.

This output-only contract accepts the common Chat/Responses values `auto`,
`default`, `flex`, `scale`, `priority`, and `fast`, preserving the exact reported
value. Unknown vendor values, nonstrings and protocol-specific values such as
Responses `ultrafast` fail the bounded portable contract rather than being
renamed. A reported value is provider metadata, not independent billing proof.

Completed JSON copies this observation to generated Chat/Responses output.
For streaming, Protoverta emits it only on terminal output (and the Chat usage
postlude), after the source has finished. Earlier generated snapshots omit it.
This avoids presenting a Responses created snapshot's requested/provisional tier
as the final serving mode. Chat can report the tier in an early chunk or a late
usage chunk; repeated nonnull values must agree. Conflicts or invalid values
fail without a fabricated successful terminal event.

Messages has no equivalent field under this contract. Translated Messages
output does not advertise an OpenAI serving tier; its native fields and native
passthrough remain unchanged. Serving-tier metadata is observational, not
reasoning state or an instruction to the model. This does not enable translated
`service_tier` requests: unsupported request controls are still rejected.

The official OpenResponses baseline fixture supplies no tier, so the existing
12 tier findings remain. This implementation preserves available source facts;
it does not manufacture facts to turn that audit green.

References checked 2026-11-04:
[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create),
[Responses](https://developers.openai.com/api/reference/python/resources/responses/methods/retrieve).
The official documentation distinguishes requested processing mode from the
mode reported in a response; they can differ. Newer protocol-specific values
require a separately reviewed target mapping.
