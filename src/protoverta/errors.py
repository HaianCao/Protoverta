"""Public errors shared by wire-format converters."""


class ConversionError(ValueError):
    """A request cannot be converted without changing its meaning."""
