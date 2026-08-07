"""Hermetic E2E harness fixtures."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from tests.v2.conftest import EGRESS_GUARD_KEY
from tests.v2.e2e.fakes import LocalControlHarness


@pytest.fixture
def harness_factory(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> Callable[..., LocalControlHarness]:
    """Build independently named harnesses under the active egress guard."""
    guard = request.config.stash[EGRESS_GUARD_KEY]
    counter = 0

    def factory(**kwargs: Any) -> LocalControlHarness:
        nonlocal counter
        counter += 1
        root = tmp_path / f"harness-{counter}"
        root.mkdir()
        return LocalControlHarness(
            root=root,
            validate_config=guard.validate_config,
            **kwargs,
        )

    return factory


@pytest_asyncio.fixture
async def harness(
    harness_factory: Callable[..., LocalControlHarness],
) -> AsyncIterator[LocalControlHarness]:
    """Start one enabled, known-identity ONLINE transaction harness."""
    runtime = harness_factory()
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()
