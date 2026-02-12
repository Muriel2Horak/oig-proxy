"""Tests for backoff module."""

import pytest

from backoff import BackoffStrategy


def test_backoff_strategy_default_params():
    """Test BackoffStrategy with default parameters."""
    strategy = BackoffStrategy()
    assert strategy.max_retries == 3
    assert strategy.initial_backoff_s == 1.0
    assert strategy.max_backoff_s == 10.0
    assert strategy.backoff_multiplier == 2.0
    assert strategy._attempt == 0


def test_backoff_strategy_custom_params():
    """Test BackoffStrategy with custom parameters."""
    strategy = BackoffStrategy(
        max_retries=5,
        initial_backoff_s=2.0,
        max_backoff_s=30.0,
        backoff_multiplier=3.0,
    )
    assert strategy.max_retries == 5
    assert strategy.initial_backoff_s == 2.0
    assert strategy.max_backoff_s == 30.0
    assert strategy.backoff_multiplier == 3.0


def test_backoff_strategy_get_delay():
    """Test BackoffStrategy.get_delay returns correct delays."""
    strategy = BackoffStrategy(
        initial_backoff_s=1.0,
        max_backoff_s=10.0,
        backoff_multiplier=2.0,
    )
    
    # First attempt
    assert strategy.get_backoff_delay() == 1.0
    
    strategy.record_failure()
    # Second attempt
    assert strategy.get_backoff_delay() == 2.0
    
    strategy.record_failure()
    # Third attempt
    assert strategy.get_backoff_delay() == 4.0
    
    strategy.record_failure()
    # Fourth attempt (should be 8.0, less than max_backoff)
    assert strategy.get_backoff_delay() == 8.0
    
    strategy.record_failure()
    # Fifth attempt (still below max_backoff)
    assert strategy.get_backoff_delay() == 10.0


def test_backoff_strategy_should_retry():
    """Test BackoffStrategy.should_retry."""
    strategy = BackoffStrategy(max_retries=3)
    
    # Initially should retry
    assert strategy.should_retry() is True
    
    # After 3 failures, should not retry
    strategy.record_failure()
    assert strategy.should_retry() is True
    
    strategy.record_failure()
    assert strategy.should_retry() is True
    
    strategy.record_failure()
    assert strategy.should_retry() is False


def test_backoff_strategy_reset():
    """Test BackoffStrategy.reset."""
    strategy = BackoffStrategy(max_retries=3)
    
    strategy.record_failure()
    strategy.record_failure()
    assert strategy._attempt == 2
    assert strategy.should_retry() is True
    
    strategy.reset()
    assert strategy._attempt == 0
    assert strategy.should_retry() is True
