"""Allowlisted environment for keyless validation processes, never live probes."""

import os
from collections.abc import Mapping

_ESSENTIAL = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERNAME",
        "USER",
        "LOGNAME",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PROGRAMDATA",
        "ALLUSERSPROFILE",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "CI",
        "GITHUB_ACTIONS",
        "GITHUB_WORKSPACE",
        "TERM",
        "NO_COLOR",
    }
)


def sanitized_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Do not pass credentials, proxies, telemetry or provider settings to tests.

    Explicit synthetic test variables and import paths may be added by callers
    AFTER sanitization. This returns a copy; it never changes the parent process.
    """
    values = os.environ if source is None else source
    result = {key: value for key, value in values.items() if key.upper() in _ESSENTIAL}
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    return result
