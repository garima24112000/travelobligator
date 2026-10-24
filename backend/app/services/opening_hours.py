"""Opening hours, read conservatively from the provider's own `opening_hours`
value (M2 Phase 3, Workstream B).

Evidence: Geoapify Place Details documents an `opening_hours` property; Places
answers carry the underlying OpenStreetMap record, whose `opening_hours` tag is
the same value in the OpenStreetMap opening_hours syntax. Nothing here invents
an hour: a value is either understood completely, or it is `unsupported` and
says nothing.

Supported (the common subset of the syntax):

  * `24/7`;
  * rules separated by `;`, each `[months] [weekdays] [times|off|closed]`, where
    a later rule replaces what earlier rules said about the dates it names, and
    additional rules after `, ` (a comma, a SPACE, then a weekday), which add to
    what was said;
  * weekdays `Mo Tu We Th Fr Sa Su`, ranges (`Mo-Fr`, wrapping `Fr-Mo`) and
    comma lists; `PH` (public holiday). A selector holding only weekdays
    followed by `, ` and more weekdays (`Mo-Fr, Su 10:00-18:00`) is ONE list --
    the way mappers write it; read the other way (all day on the first days),
    it would give the same open/closed answer for every date anyway;
  * months and month-day ranges before the weekdays (`Jan-Mar`, `Apr 1-Jun 30`,
    `Dec 24-26`, comma lists, ranges across the year end such as `Nov 1-Mar 31`);
  * times `HH:MM-HH:MM`, comma lists, ends up to `48:00`, and overnight spans
    (`22:00-02:00`), which continue into the next date;
  * a quoted comment after `off` / `closed` (`closed "temporarily closed"`):
    the rule still closes; a comment anywhere else changes the meaning
    ("by appointment") and is not supported.

Everything else -- years, week numbers, `SH` (school holidays, of which no data
exists), `sunrise`/`sunset`, open ends (`+`), `||` fallbacks, `Mo[1]`,
comments on open rules, `unknown` -- makes the WHOLE value `unsupported`, and
`unsupported_feature` names which of those it was (a fixed label, never the
value): a half-understood value could call a place closed (or open) on a date
it is not.

Semantics on one date: dates the value does not cover are closed (the syntax's
own rule); `off`/`closed` closes the named dates; a `PH` rule applies on a
public holiday. A date is a public holiday only when the trip's holiday data,
which came from a holiday provider, says so; without that data a value that
has a `PH` rule gives `unknown` for every date, because any date might be one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

OPEN = "open"
CLOSED = "closed"
UNKNOWN = "unknown"

_DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_DAY_INDEX = {name: index for index, name in enumerate(_DAYS)}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_INDEX = {name: index + 1 for index, name in enumerate(_MONTHS)}
_TIME_SPAN = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
_DAY_TOKEN = re.compile(r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$")
_WEEKDAY_SELECTOR = re.compile(r"^(?:(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?|PH)(?:,(?:(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:-(?:Mo|Tu|We|Th|Fr|Sa|Su))?|PH))*$")
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_MONTH_ITEM = rf"{_MONTH}(?:\s+\d{{1,2}})?(?:-(?:{_MONTH}(?:\s+\d{{1,2}})?|\d{{1,2}}))?"
_MONTH_PREFIX = re.compile(rf"^({_MONTH_ITEM}(?:,{_MONTH_ITEM})*)(?:\s+(.*))?$")
_MONTH_ITEM_PARTS = re.compile(rf"^({_MONTH})(?:\s+(\d{{1,2}}))?(?:-(?:({_MONTH})(?:\s+(\d{{1,2}}))?|(\d{{1,2}})))?$")
_MAX_LENGTH = 255  # the OpenStreetMap tag-value limit
# `, ` that starts an ADDITIONAL rule (a space, then a weekday or PH), not a list item
_ADDITIONAL_RULE = re.compile(r",\s+(?=(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)\b)")
# A quoted comment directly after `off` / `closed` keeps the rule closing.
_CLOSING_COMMENT = re.compile(r'\b(off|closed)\s+"[^"]*"', re.IGNORECASE)

# Fixed labels for what made a value unsupported (`unsupported_feature`).
FEATURE_FALLBACK = "fallback_rule"
FEATURE_COMMENT = "comment"
FEATURE_SUN_TIMES = "sunrise_sunset"
FEATURE_OPEN_END = "open_end"
FEATURE_SCHOOL_HOLIDAY = "school_holiday"
FEATURE_YEAR_OR_WEEK = "year_or_week"
FEATURE_NTH_WEEKDAY = "nth_weekday"
FEATURE_TOO_LONG = "too_long"
FEATURE_OTHER = "other_syntax"


class UnsupportedHours(ValueError):
    """The value uses syntax this reader does not understand completely."""


@dataclass(frozen=True)
class _Rule:
    weekdays: frozenset[int] | None  # None: every day
    holiday: bool  # the rule names PH
    spans: tuple[tuple[int, int], ...]  # minutes from the start of the day; end may pass 24:00
    closed: bool
    additive: bool = False  # an additional rule (`, `): adds, never replaces
    # ((start month, day), (end month, day)) ranges; None: every date
    months: tuple[tuple[tuple[int, int], tuple[int, int]], ...] | None = None


@dataclass(frozen=True)
class OpeningHours:
    """A completely understood value."""

    rules: tuple[_Rule, ...]
    always_open: bool = False
    has_holiday_rule: bool = False


@dataclass(frozen=True)
class DayHours:
    """What the value says about one date."""

    status: str  # OPEN / CLOSED / UNKNOWN
    # (start, end) minutes within this date, 0..1440, in order; empty unless OPEN
    intervals: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    reason: str | None = None  # why UNKNOWN
    feature: str | None = None  # for an unsupported value: what made it so (a fixed label)


def _minutes(hours: str, minutes: str) -> int:
    hour, minute = int(hours), int(minutes)
    if minute > 59 or hour > 48 or (hour == 48 and minute):
        raise UnsupportedHours("time out of range")
    return hour * 60 + minute


def _parse_weekdays(selector: str) -> tuple[frozenset[int] | None, bool]:
    days: set[int] = set()
    holiday = False
    for token in selector.split(","):
        token = token.strip()
        if token == "PH":
            holiday = True
            continue
        match = _DAY_TOKEN.match(token)
        if not match:
            raise UnsupportedHours("weekday selector")
        start = _DAY_INDEX[match.group(1)]
        end = _DAY_INDEX[match.group(2)] if match.group(2) else start
        index = start
        while True:
            days.add(index)
            if index == end:
                break
            index = (index + 1) % 7
    return (frozenset(days) if days else None), holiday


def _parse_months(selector: str) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
    ranges = []
    for item in selector.split(","):
        match = _MONTH_ITEM_PARTS.match(item.strip())
        if not match:
            raise UnsupportedHours("month selector")
        first_month, first_day, last_month, last_day, same_month_day = match.groups()
        start_month = _MONTH_INDEX[first_month]
        start_day = int(first_day) if first_day else 1
        if same_month_day is not None:  # `Dec 24-26`
            if first_day is None:
                raise UnsupportedHours("day range without a start day")
            end = (start_month, int(same_month_day))
        elif last_month is not None:  # `Apr 1-Jun 30`, `Jan-Mar`
            end = (_MONTH_INDEX[last_month], int(last_day) if last_day else 31)
        else:  # `Jan`, `Dec 25`
            end = (start_month, int(first_day) if first_day else 31)
        for _, day in ((start_month, start_day), end):
            if not 1 <= day <= 31:
                raise UnsupportedHours("day of month out of range")
        ranges.append(((start_month, start_day), end))
    return tuple(ranges)


def _parse_spans(selector: str) -> tuple[tuple[int, int], ...]:
    spans = []
    for token in selector.split(","):
        match = _TIME_SPAN.match(token.strip())
        if not match:
            raise UnsupportedHours("time selector")
        start = _minutes(match.group(1), match.group(2))
        end = _minutes(match.group(3), match.group(4))
        if start >= 24 * 60:
            raise UnsupportedHours("start after midnight")
        if end <= start:
            end += 24 * 60  # an overnight span
        if end - start > 24 * 60:
            raise UnsupportedHours("span longer than a day")
        spans.append((start, end))
    return tuple(spans)


def _pieces(text: str) -> list[tuple[str, bool]]:
    """(rule text, additive) in order. A piece that is ONLY a weekday list,
    followed by an additional piece, continues that list (`Mo-Fr, Su ...`)."""
    pieces: list[tuple[str, bool]] = []
    for raw_rule in text.split(";"):
        parts = _ADDITIONAL_RULE.split(raw_rule)
        merged: list[str] = []
        carry = ""
        for part in parts:
            part = part.strip()
            if _WEEKDAY_SELECTOR.match(part):
                carry = f"{carry},{part}" if carry else part
                continue
            merged.append(f"{carry},{part}" if carry else part)
            carry = ""
        if carry:
            merged.append(carry)  # a weekday list with nothing after it: rejected below
        for position, part in enumerate(merged):
            pieces.append((part, position > 0))
    return pieces


def parse_opening_hours(value: str | None) -> OpeningHours:
    """Parses a value completely, or raises `UnsupportedHours`."""
    text = (value or "").strip()
    if not text or len(text) > _MAX_LENGTH or "||" in text:
        raise UnsupportedHours("empty, too long or fallback")
    text = _CLOSING_COMMENT.sub(lambda match: match.group(1), text)
    if '"' in text:
        raise UnsupportedHours("comment")
    if text == "24/7":
        return OpeningHours(rules=(), always_open=True)
    rules: list[_Rule] = []
    for raw_rule, additive in _pieces(text):
        part = raw_rule.strip()
        if not part:
            continue
        closed = False
        for word in ("off", "closed"):
            if part.lower() == word or part.lower().endswith(" " + word):
                closed = True
                part = part[: -len(word)].strip()
                break
        months = None
        month_match = _MONTH_PREFIX.match(part) if part[:3] in _MONTH_INDEX else None
        if month_match:
            months = _parse_months(month_match.group(1))
            part = (month_match.group(2) or "").strip()
        tokens = part.split(" ") if part else []
        weekdays: frozenset[int] | None = None
        holiday = False
        spans: tuple[tuple[int, int], ...] = ()
        if tokens and (_DAY_TOKEN.match(tokens[0].split(",")[0]) or tokens[0].split(",")[0] == "PH"):
            weekdays, holiday = _parse_weekdays(tokens.pop(0))
        if tokens:
            spans = _parse_spans(" ".join(tokens).replace(" ", ""))
        if tokens and closed:
            raise UnsupportedHours("times with off")
        if not closed and not spans:
            raise UnsupportedHours("a selector without times")  # e.g. `Mo-Fr open`: not in the subset
        if additive and closed:
            raise UnsupportedHours("additional rule with off")
        rules.append(_Rule(weekdays=weekdays, holiday=holiday, spans=spans, closed=closed, additive=additive,
                           months=months))
    if not rules:
        raise UnsupportedHours("no rule")
    return OpeningHours(rules=tuple(rules), has_holiday_rule=any(rule.holiday for rule in rules))


def unsupported_feature(value: str | None) -> str | None:
    """A fixed label for what makes `value` unsupported, or None when it is
    supported (or empty). Diagnostics only: never the value itself."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parse_opening_hours(text)
        return None
    except UnsupportedHours:
        pass
    if len(text) > _MAX_LENGTH:
        return FEATURE_TOO_LONG
    if "||" in text:
        return FEATURE_FALLBACK
    if '"' in _CLOSING_COMMENT.sub(lambda match: match.group(1), text):
        return FEATURE_COMMENT
    lowered = text.lower()
    if "sunrise" in lowered or "sunset" in lowered or "dawn" in lowered or "dusk" in lowered:
        return FEATURE_SUN_TIMES
    if "+" in text:
        return FEATURE_OPEN_END
    if re.search(r"\bSH\b", text):
        return FEATURE_SCHOOL_HOLIDAY
    if re.search(r"\b(?:19|20)\d{2}\b|\bweek\b", text):
        return FEATURE_YEAR_OR_WEEK
    if re.search(r"\[[^\]]*\]", text):
        return FEATURE_NTH_WEEKDAY
    return FEATURE_OTHER


def _in_months(rule: _Rule, day: date) -> bool:
    if rule.months is None:
        return True
    point = (day.month, day.day)
    for start, end in rule.months:
        if start <= end:
            if start <= point <= end:
                return True
        elif point >= start or point <= end:  # across the year end
            return True
    return False


def _rule_applies(rule: _Rule, day: date, is_holiday: bool) -> bool:
    if not _in_months(rule, day):
        return False
    weekday = day.weekday()
    if rule.holiday and rule.weekdays is None:
        return is_holiday
    if rule.weekdays is None:
        return True
    return weekday in rule.weekdays or (rule.holiday and is_holiday)


def _spans_on(hours: OpeningHours, day: date, is_holiday: bool) -> tuple[tuple[int, int], ...]:
    """The spans starting on `day` (minutes; an end may pass 24:00). Later
    rules replace earlier ones for the dates they name."""
    spans: tuple[tuple[int, int], ...] = ()
    for rule in hours.rules:
        if _rule_applies(rule, day, is_holiday):
            if rule.additive:
                spans = spans + rule.spans
            else:
                spans = () if rule.closed else rule.spans
    return spans


def hours_on(value: str | None, day: date, holidays: frozenset[date] | None) -> DayHours:
    """What the provider's value says about `day`. `holidays` is the set of
    public holidays from the trip's holiday data, or None when no such data
    exists (then a value with a `PH` rule says nothing)."""
    try:
        hours = parse_opening_hours(value)
    except UnsupportedHours:
        if not (value or "").strip():
            return DayHours(status=UNKNOWN, reason="hours_not_listed")
        return DayHours(status=UNKNOWN, reason="hours_not_understood", feature=unsupported_feature(value))
    if hours.always_open:
        return DayHours(status=OPEN, intervals=((0, 24 * 60),))
    if hours.has_holiday_rule and holidays is None:
        return DayHours(status=UNKNOWN, reason="holiday_data_unavailable")

    def holiday(d: date) -> bool:
        return bool(holidays) and d in holidays

    intervals: list[tuple[int, int]] = []
    previous = day - timedelta(days=1)
    for start, end in _spans_on(hours, previous, holiday(previous)):
        if end > 24 * 60:  # yesterday's overnight span continues into today
            intervals.append((0, end - 24 * 60))
    for start, end in _spans_on(hours, day, holiday(day)):
        intervals.append((start, min(end, 24 * 60)))
    intervals = sorted(span for span in intervals if span[1] > span[0])
    if not intervals:
        return DayHours(status=CLOSED)
    return DayHours(status=OPEN, intervals=tuple(intervals))


def format_intervals(intervals: tuple[tuple[int, int], ...]) -> list[str]:
    """`["09:00–17:00", ...]`; 24:00 is written as such."""
    def clock(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return [f"{clock(start)}–{clock(end)}" for start, end in intervals]


def holiday_dates(planning_state: Any) -> frozenset[date] | None:
    """The trip's public holidays from the holiday provider, or None when no
    such data exists (then a `PH` hours rule says nothing about any date).
    The holidays only decide whether a place's OWN `PH` rule applies; they
    are never a reason to call a place open or closed."""
    from app.models.common import DataStatus

    context = getattr(planning_state, "holiday_context", None)
    if context is None or context.data_status not in (DataStatus.LIVE, DataStatus.CACHED):
        return None
    return frozenset(holiday.date for holiday in context.holidays)
