"""Sphinx configuration for the Protoverta documentation site."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

project = "Protoverta"
author = "HaianCao"
copyright = "HaianCao"
try:
    import tomllib

    release = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
except (OSError, KeyError):
    release = "unknown"
version = release

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinxarg.ext",
    "sphinx_design",
]
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
root_doc = "index"
exclude_patterns = ["_build", "README.md"]

myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {"members": True, "show-inheritance": False}
napoleon_google_docstring = False
napoleon_numpy_docstring = False

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

html_theme = "furo"
html_title = f"Protoverta {release}"
html_static_path = ["_static"]
html_css_files = [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap",
    "protoverta.css",
]
# One transparent logo reads on light and dark backgrounds.
html_logo = "_static/protoverta_logo.png"
html_favicon = "_static/protoverta_logo.png"
_BRAND = {
    "font-stack": "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    "font-stack--monospace": "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
}
html_theme_options = {
    "light_css_variables": {
        **_BRAND,
        "color-brand-primary": "#4f46e5",
        "color-brand-content": "#4338ca",
        "color-brand-visited": "#6d28d9",
        "color-admonition-title-background--note": "rgba(79, 70, 229, 0.1)",
    },
    "dark_css_variables": {
        **_BRAND,
        "color-brand-primary": "#a5b4fc",
        "color-brand-content": "#a5b4fc",
        "color-brand-visited": "#d8b4fe",
        "color-background-primary": "#0f1117",
        "color-background-secondary": "#151824",
    },
    "source_repository": "https://github.com/HaianCao/Protoverta/",
    "source_branch": "main",
    "source_directory": "docs/",
}

# Links in the guides point at repository files outside the docs tree.
suppress_warnings = ["myst.xref_missing", "myst.domains"]
linkcheck_ignore = [r"https://github\.com/HaianCao/.*/(security|issues|compare)/.*"]
