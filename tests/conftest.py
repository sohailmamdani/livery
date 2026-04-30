"""Pytest auto-fixtures — blocks real network calls in tests.

Same policy as branddb/tests/conftest.py: tests must not hit external
services. If a test needs HTTP, mock at a higher level.
"""

from __future__ import annotations

import pytest


class _BlockedNetworkCall(RuntimeError):
    """Raised when a test tries to make a real network call."""


@pytest.fixture(autouse=True)
def _block_urllib_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test triggers a real urllib request.

    We don't block here via patch("urllib.request.urlopen") because many tests
    legitimately patch urlopen themselves — doing it globally would conflict.
    Instead we install a guard that only fires if the local patch DOESN'T
    replace it. In practice: tests that use patch("urllib.request.urlopen", ...)
    override this fixture's patch locally; tests that forget to patch hit
    the guard and fail loud.
    """
    import urllib.request

    original_urlopen = urllib.request.urlopen

    def _blocked(*args: object, **kwargs: object) -> object:
        raise _BlockedNetworkCall(
            "Real HTTP call attempted in test. Patch urllib.request.urlopen "
            "with a mock before invoking the code under test."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
