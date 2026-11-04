# P12 — Provider and Model Profiles (2026-11-04)

## Scope

Milestone `P12` hardens evidence-scoped provider/model profiles (`ProviderProfile` and `ProviderProfiles`), evidence identity locking (`evidence_identity.py`), reversible injective parameter aliasing, schema integrity (`required`, `additionalProperties`, boolean schemas), token cap / reasoning effort preflight invariants, and strict unknown vendor field handling.

## Key Changes & Verification

1. **Evidence & Identity Locking (`src/protoverta/provider_profiles.py`, `src/protoverta/evidence_identity.py`)**:
   - Every `ProviderProfile` requires an exact HTTPS/loopback `base_url` prefix, `Protocol.CHAT`, exact `model` ID, ISO `checked_at` date, non-empty `evidence`, and proven `token_budget_semantics="completion_total"`.
   - `ProviderProfiles.fingerprint()` hashes all profile fields recursively (including nested mappings such as `reasoning_values` and `parameter_aliases`), automatically updating `route_identity(config)` whenever any profile contract changes.
2. **Injective, Reversible Parameter Name Aliasing (`src/protoverta/provider_profiles.py`, `src/protoverta/server.py`, `src/protoverta/stream_pipe.py`)**:
   - Added optional `parameter_aliases: Mapping[str, Mapping[str, str]] | None` to `ProviderProfile`, enforcing injective mappings with disjoint source and target parameter names.
   - `ProviderProfiles.apply()` remaps matching tool schema `properties` and `required` arrays as well as historical assistant `tool_calls` JSON object arguments without touching arbitrary message text, descriptions, or tool outputs.
   - `ProviderProfiles.restore()` and `ProviderProfiles.stream_delta_restorer()` reversibly restore original parameter names on both completed JSON responses and translated SSE streams.
3. **No Schema Stripping, No Clamping, No Blind Vendor Passthrough (`tests/test_p12_provider_profiles.py`)**:
   - Verified that boolean schemas (`false`), `required`, and `additionalProperties` are preserved intact through conversion and profile application.
   - Verified that over-budget `max_output_tokens` and uncertified `reasoning_effort` fail closed at capability/profile preflight (`HTTP 422`) rather than clamping or downgrading.
   - Verified that unknown vendor fields are rejected across all protocols (`ConversionError`).
