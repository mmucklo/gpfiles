"""
Tests for compute_expiry and ModelSignal expiration_date defaults.
Run with: python3 test_expiration.py
"""
from datetime import date, timedelta
from consensus import compute_expiry, ModelSignal, SignalDirection, ModelType


def test_compute_expiry():
    for dte_label, dte_days in [("0DTE", 0), ("7DTE", 7), ("14DTE", 14)]:
        result = compute_expiry(dte_label)
        expiry = date.fromisoformat(result)

        # Must not be in the past
        assert expiry >= date.today(), f"{dte_label}: expiry {result} is in the past"

        # Must be a Friday (weekday 4)
        assert expiry.weekday() == 4, f"{dte_label}: expiry {result} is not a Friday (weekday={expiry.weekday()})"

        # Must be at least N days from today
        assert expiry >= date.today() + timedelta(days=dte_days), (
            f"{dte_label}: expiry {result} is less than {dte_days} days from today"
        )

    print("test_compute_expiry: PASSED")


def test_model_signal_default():
    sig = ModelSignal(
        model_id=ModelType.SENTIMENT,
        direction=SignalDirection.BULLISH,
        confidence=0.91,
        timestamp=0.0,
    )
    expiry = date.fromisoformat(sig.expiration_date)

    assert expiry >= date.today(), f"Default expiration_date {sig.expiration_date} is in the past"
    assert expiry.weekday() == 4, f"Default expiration_date {sig.expiration_date} is not a Friday"

    print("test_model_signal_default: PASSED")


if __name__ == "__main__":
    test_compute_expiry()
    test_model_signal_default()
    print("All tests passed.")
