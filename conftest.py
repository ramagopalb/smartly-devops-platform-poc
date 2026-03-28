"""
conftest.py — Fix sys.path to prevent platform/ directory from shadowing Python stdlib.
The platform/ folder has the same name as Python's stdlib 'platform' module.
This conftest ensures stdlib platform is importable and platform/ modules are importable by short name.
"""
import sys
import os
import importlib

# Remove the POC root from sys.path early entries to prevent platform/ shadowing stdlib.
# We then re-add individual paths as needed.
_poc_root = os.path.dirname(os.path.abspath(__file__))
_platform_dir = os.path.join(_poc_root, "devops_platform")

# Remove POC root from sys.path if it would shadow stdlib 'platform'
# (pytest adds cwd to sys.path[0])
sys.path = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != os.path.normcase(_poc_root)]

# Add platform/ directory contents to sys.path so tests can do `from namespace_controller import ...`
if _platform_dir not in sys.path:
    sys.path.insert(0, _platform_dir)

# Force-reimport stdlib platform if it was cached as our local module
if "platform" in sys.modules:
    mod = sys.modules["platform"]
    if not hasattr(mod, "system"):
        del sys.modules["platform"]
        import platform  # noqa: F401 — force reload stdlib platform

collect_ignore = ["platform"]
collect_ignore_glob = ["platform/*"]
