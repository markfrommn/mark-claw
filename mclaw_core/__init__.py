"""mclaw_core — shared modules for the mark-claw tooling layer.

This package holds the CLI and the small, dependency-free helpers the
launchd-invoked wrappers reuse. It contains no personal data and resolves no
secrets except inside the secret wrapper (``mclaw_core.secret``), per the
CLAUDE.md hard rules.
"""

__version__ = "0.1.0"
