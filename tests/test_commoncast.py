"""Initial smoke test for CommonCast package."""

import importlib


def test_can_import_commoncast():
    """Ensure the commoncast package can be imported."""
    module = importlib.import_module("commoncast")
    assert module is not None
