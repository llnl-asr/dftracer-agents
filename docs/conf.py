from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath("../src"))

project = "dftracer-agents"
copyright = "2026, dftracer-agents"
author = "dftracer-agents"

extensions = ["sphinx.ext.autodoc", "sphinx.ext.napoleon"]
templates_path = ["_templates"]
exclude_patterns = ["_build"]

html_theme = "alabaster"
html_static_path = ["_static"]
