"""Public errors shared by wire-format converters."""


class ConversionError(ValueError):
    """A request cannot be converted without changing its meaning."""


class UnsupportedFeatureError(ConversionError):
    """Recognized wire semantics without an equivalent enabled target mapping."""

    def __init__(self, feature: str) -> None:
        messages = {
            "refusal": "refusal cannot be preserved across protocols under the current strict policy",
            "content_filter": "content-filter termination cannot be preserved across protocols under the current strict policy",
            "stop_sequence": "matched stop-sequence termination cannot be preserved across protocols under the current strict policy",
            "stream_usage_policy": "translated stream_options requires streaming and include_usage=true",
            "codex_closed_resume": "Closed child resume may switch the configured child model in Codex 0.166.0. Use send_input on a retained child, or start a new child; resume_agent is unsupported for different child models.",
        }
        if feature not in messages:
            raise ValueError("unknown unsupported-feature category")
        self.feature = feature
        super().__init__(messages[feature])
