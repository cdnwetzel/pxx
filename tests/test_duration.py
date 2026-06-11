"""Tests for pxx.duration — human-readable durations (seeded for loop dogfood #009)."""


def test_seconds_only():
    from pxx.duration import human_duration

    assert human_duration(45) == "45s"


def test_minutes_and_seconds_zero_padded():
    from pxx.duration import human_duration

    assert human_duration(125) == "2m05s"


def test_hours_and_minutes_no_seconds():
    from pxx.duration import human_duration

    assert human_duration(3720) == "1h02m"
