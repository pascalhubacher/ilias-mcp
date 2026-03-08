"""
Unit tests for server.py helpers: RefId (domain), RateLimiter (interface layer).
"""

import time
import pytest

from domain.models import RefId
from interface.context import RateLimiter


# ---------------------------------------------------------------------------
# RefId (domain value object — validates on construction)
# ---------------------------------------------------------------------------

class TestRefId:
    def test_valid_integer_string(self):
        RefId("42")
        RefId("1")
        RefId("99999")

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            RefId("0")

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            RefId("-1")

    def test_float_string_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            RefId("3.14")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            RefId("")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            RefId("abc")

    def test_injection_attempt_raises(self):
        with pytest.raises(ValueError, match="positive integer"):
            RefId("42; DROP TABLE courses")

    def test_str_returns_value(self):
        assert str(RefId("42")) == "42"


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_first_call_passes(self):
        rl = RateLimiter()
        rl.check("login")

    def test_second_immediate_call_raises(self):
        rl = RateLimiter()
        rl.check("login")
        with pytest.raises(RuntimeError, match="Rate limit"):
            rl.check("login")

    def test_different_tools_are_independent(self):
        rl = RateLimiter()
        rl.check("login")
        rl.check("list_courses")  # different tool — must not raise

    def test_call_allowed_after_interval(self):
        rl = RateLimiter()
        rl.check("login")
        rl._last_calls["login"] = time.monotonic() - 999
        rl.check("login")  # must not raise

    def test_error_message_includes_wait_time(self):
        rl = RateLimiter()
        rl.check("download_all_files")
        with pytest.raises(RuntimeError, match=r"Please wait \d+\.\ds"):
            rl.check("download_all_files")
