"""Runtime identity: which exact Hermes build/integration is actually running.

This is a different question from `_hermes_version()` in `api_server.py`
(the single "version" string exposed on `/health`). A source checkout can
have a `hermes_cli.__version__` that has moved ahead of the last package
release (`importlib.metadata`), and separately, this fork carries an
`personal-system-memory` integration line — additive work layered on top of
hermes-agent, tracked by its own commit and structure version rather than by
a package release. When something looks wrong in production, "which package
version" and "which source version" and "which integration commit" can all
give different, individually correct answers; this module reports all three
together instead of forcing a single field to stand in for all of them.

`STRUCTURE_ID`, `STRUCTURE_VERSION`, `INTEGRATION_COMMIT`, and `CHANNEL`
describe the integration structure this code was built from, not the
package. Bump `STRUCTURE_VERSION` and update `INTEGRATION_COMMIT` when the
`personal-system-memory` integration line moves to a new commit.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

STRUCTURE_ID = "personal-system-memory"
STRUCTURE_VERSION = 1
INTEGRATION_COMMIT = "422be8696f9b7a6247d9b23209f263a50ce96343"
CHANNEL = "integration"


def get_package_version() -> Optional[str]:
    """Return the installed ``hermes-agent`` package version, or None.

    None (not an exception) if the package metadata isn't available — e.g. a
    source checkout that was never `pip install`-ed. A version probe must
    never raise.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("hermes-agent")
        except PackageNotFoundError:
            return None
    except Exception:
        return None


def get_source_version() -> Optional[str]:
    """Return ``hermes_cli.__version__`` from the in-tree source, or None."""
    try:
        from hermes_cli import __version__

        return __version__
    except Exception:
        return None


def get_runtime_identity() -> Dict[str, Any]:
    """Return the full runtime identity record.

    Always succeeds — every probe inside is individually exception-safe, so
    this is safe to call from a health endpoint on every request.
    """
    return {
        "structure_id": STRUCTURE_ID,
        "structure_version": STRUCTURE_VERSION,
        "integration_commit": INTEGRATION_COMMIT,
        "channel": CHANNEL,
        "package_version": get_package_version(),
        "source_version": get_source_version(),
    }
