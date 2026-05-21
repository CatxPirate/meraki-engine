"""Unit tests — pure logic, no external dependencies."""

import pytest

from meraki_engine.config.settings import Settings
from meraki_engine.config.constants import FallbackOrder, VerifyStrategy


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.retry_limit == 3
        assert s.viewport_width == 1920
        assert s.viewport_height == 1080
        assert s.verify_timeout == 5000

    def test_custom_override(self):
        s = Settings(retry_limit=5, verify_timeout=10000)
        assert s.retry_limit == 5
        assert s.verify_timeout == 10000
        assert s.viewport_width == 1920  # unchanged


class TestConstants:
    def test_fallback_order_values_unique(self):
        values = [e.value for e in FallbackOrder]
        assert len(values) == len(set(values)), "enum values must be unique"

    def test_verify_strategy_values(self):
        assert VerifyStrategy.DOM_CHANGE.value == "dom_change"
        assert VerifyStrategy.VISUAL_DIFF.value == "visual_diff"
