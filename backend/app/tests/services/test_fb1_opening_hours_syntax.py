"""Final Batch 1 (E): the opening-hours syntax kinds added after the acceptance
run -- the n-th weekday of the month and the `:` after a month selector.

Synthetic values and dates only (August 2026: the 1st is a Saturday, Mondays are
the 3rd, 10th, 17th, 24th and 31st). Anything still not understood completely
says nothing."""

from __future__ import annotations

from datetime import date

import pytest

from app.services import opening_hours as oh

NO_HOLIDAYS: frozenset[date] = frozenset()


@pytest.mark.parametrize(
    ("value", "day", "status"),
    [
        ("Mo[1] 10:00-12:00", date(2026, 8, 3), oh.OPEN),
        ("Mo[1] 10:00-12:00", date(2026, 8, 10), oh.CLOSED),  # the second Monday: not named
        ("Mo[-1] 10:00-12:00", date(2026, 8, 31), oh.OPEN),
        ("Mo[-1] 10:00-12:00", date(2026, 8, 24), oh.CLOSED),
        ("Mo[2-3] 10:00-12:00", date(2026, 8, 17), oh.OPEN),
        ("Mo[1,3] 10:00-12:00", date(2026, 8, 10), oh.CLOSED),
        ("Mo[1,3] 10:00-12:00", date(2026, 8, 17), oh.OPEN),
        # a closing rule on one occurrence replaces the weekly rule on that date only
        ("Tu-Su 10:00-18:00; Su[1] off", date(2026, 8, 2), oh.CLOSED),
        ("Tu-Su 10:00-18:00; Su[1] off", date(2026, 8, 9), oh.OPEN),
        # a weekday named plainly as well is open on every occurrence
        ("Mo,Mo[1] 10:00-12:00", date(2026, 8, 10), oh.OPEN),
        ("Sa,Su[1] 10:00-12:00", date(2026, 8, 9), oh.CLOSED),
        ("Sa,Su[1] 10:00-12:00", date(2026, 8, 2), oh.OPEN),
    ],
)
def test_nth_weekday(value: str, day: date, status: str) -> None:
    assert oh.hours_on(value, day, NO_HOLIDAYS).status == status


@pytest.mark.parametrize(
    ("value", "day", "status", "intervals"),
    [
        ("Apr 1-Oct 31: 09:00-18:00; Nov 1-Mar 31: 10:00-16:00", date(2026, 8, 12), oh.OPEN, ["09:00–18:00"]),
        ("Apr 1-Oct 31: 09:00-18:00; Nov 1-Mar 31: 10:00-16:00", date(2026, 12, 2), oh.OPEN, ["10:00–16:00"]),
        ("Jan 01-Mar 31: Tu-Su 10:00-16:00", date(2026, 2, 2), oh.CLOSED, []),  # a Monday
        ("Jan 01-Mar 31: Tu-Su 10:00-16:00", date(2026, 8, 12), oh.CLOSED, []),  # outside the months named
    ],
)
def test_a_colon_after_the_month_selector(value: str, day: date, status: str, intervals: list[str]) -> None:
    reading = oh.hours_on(value, day, NO_HOLIDAYS)
    assert (reading.status, oh.format_intervals(reading.intervals)) == (status, intervals)


@pytest.mark.parametrize(
    ("value", "feature"),
    [
        ("Su[-1] -1 day 10:00-12:00", oh.FEATURE_NTH_WEEKDAY),  # a day offset
        ("Mo[0] 10:00-12:00", oh.FEATURE_NTH_WEEKDAY),
        ("Mo[6] 10:00-12:00", oh.FEATURE_NTH_WEEKDAY),
        ("Mo-Fr[1] 10:00-12:00", oh.FEATURE_NTH_WEEKDAY),  # n-th only on a single weekday
        ("Mo[3-1] 10:00-12:00", oh.FEATURE_NTH_WEEKDAY),
        ("Mo-Fr: 10:00-12:00", oh.FEATURE_OTHER),  # a colon after weekdays is not the syntax
    ],
)
def test_what_is_still_not_understood_says_nothing(value: str, feature: str) -> None:
    reading = oh.hours_on(value, date(2026, 8, 3), NO_HOLIDAYS)
    assert reading.status == oh.UNKNOWN and reading.reason == "hours_not_understood"
    assert oh.unsupported_feature(value) == feature
