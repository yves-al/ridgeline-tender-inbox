"""Step 7: turn the date and time words the model copied into exact windows.

Date words:
  - "11/15", "11/15/2026", "Nov 15", "November 15": that date (the email's year if none is written)
  - a weekday with a date ("Thursday 11/12") must match it, or it's unclear
  - a weekday alone: for the pickup, the next one after the email arrived; for the delivery,
    the first one on or after the pickup. The arrival weekday (today or next week?) and a delivery
    on the pickup's weekday (same day or a week later?) are unclear.
  - "today" / "tomorrow": from the day the email arrived
  - "same day" or nothing, on a change: the load's current date
Time words:
  - one time ("0930", "8 AM", "appt 1:30pm"): a one-hour appointment
  - two times ("07:00-11am", "12 PM-2pm"): that window
  - FCFS, "first come first served", "anytime", or no time: the facility's default hours
  - a stated zone ("1100 MT"): read in that zone, then given in the facility's zone
Times are the facility's local time and carry its UTC offset. Anything else is unclear.
"""
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
ZONES = {'mt': 'America/Denver', 'mst': 'America/Denver', 'mdt': 'America/Denver', 'mountain': 'America/Denver',
         'ct': 'America/Chicago', 'cst': 'America/Chicago', 'cdt': 'America/Chicago', 'central': 'America/Chicago',
         'et': 'America/New_York', 'est': 'America/New_York', 'edt': 'America/New_York', 'eastern': 'America/New_York',
         'pt': 'America/Los_Angeles', 'pst': 'America/Los_Angeles', 'pdt': 'America/Los_Angeles', 'pacific': 'America/Los_Angeles'}
OPEN_HOURS = re.compile(r'fcfs|first come|any ?time|during (business |open )?hours|business hours')
FILLER = re.compile(r'\b(appt|appointment|at|on|the|between|and|to|time|local)\b|[@,.()]')


class TimeProblem(Exception):
    """The words can't be turned into a window; reason is the review reason."""
    def __init__(self, reason, detail):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def unclear(detail):
    return TimeProblem('unclear', detail)


# ---- dates -------------------------------------------------------------------------------

def parse_date(words, received, kind, pickup_date=None, current_date=None):
    """The calendar date the words mean. kind is 'pickup' or 'delivery'."""
    text = (words or '').lower().strip()
    if not text or text == 'same day':
        if current_date:
            return current_date
        raise TimeProblem('missing_info', f'no {kind} date') if not text else unclear(f'{kind} "same day" with no load to refer to')
    if text in ('today', 'tomorrow'):
        return received + timedelta(days=0 if text == 'today' else 1)

    rest = text
    weekday = None
    found = re.search(r'\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b\.?', rest)
    if found:
        weekday = WEEKDAYS.index(found.group(1))
        rest = rest.replace(found.group(0), ' ')

    month = day = year = None
    numeric = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b', rest)
    named = re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b', rest)
    if numeric:
        month, day, year = int(numeric.group(1)), int(numeric.group(2)), numeric.group(3)
        rest = rest.replace(numeric.group(0), ' ')
    elif named:
        month, day = MONTHS.index(named.group(1)) + 1, int(named.group(2))
        rest = rest.replace(named.group(0), ' ')
    if FILLER.sub(' ', rest).strip():
        raise unclear(f'{kind} date "{words}" not understood')

    if month:
        year = int(year) + (2000 if len(year) == 2 else 0) if year else received.year
        try:
            result = date(year, month, day)
        except ValueError:
            raise unclear(f'{kind} date "{words}" is not a real date')
        if weekday is not None and result.weekday() != weekday:
            raise unclear(f'{kind} date "{words}": {result:%m/%d} is a {result:%A}')
        return result

    if weekday is None:
        raise unclear(f'{kind} date "{words}" not understood')
    start = pickup_date if kind == 'delivery' and pickup_date else received
    if weekday == start.weekday():
        what = 'the day the email arrived' if start == received else 'the pickup day'
        raise unclear(f'{kind} "{words}" is the same weekday as {what}: same day or a week later?')
    return start + timedelta(days=(weekday - start.weekday()) % 7)


# ---- times -------------------------------------------------------------------------------

TIME = re.compile(r'\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?\.?\b|\b(\d{1,2}):(\d{2})\b|\b(\d{2})(\d{2})\b|\b(noon)\b')


def parse_times(words, kind):
    """Returns (stated zone or None, [one or two (hour, minute)]), or None for default hours."""
    text = (words or '').lower().strip()
    if not text or OPEN_HOURS.search(text):
        return None
    zone = None
    for name in ZONES:
        if re.search(rf'\b{name}\b', text):
            zone, text = ZONES[name], re.sub(rf'\b{name}\b', ' ', text)
            break

    # "8-10am": the start borrows the end's am/pm.
    text = re.sub(r'(?<![\d:])\b(\d{1,2})(\s*(?:-|to)\s*\d{1,2}(?::\d{2})?\s*([ap])\.?m)', r'\1\3m\2', text)
    times = []
    for m in TIME.finditer(text):
        if m.group(8):
            times.append((12, 0, None))
        elif m.group(1):
            times.append((int(m.group(1)), int(m.group(2) or 0), m.group(3)))
        elif m.group(4):
            times.append((int(m.group(4)), int(m.group(5)), None))
        else:
            times.append((int(m.group(6)), int(m.group(7)), None))
    leftover = FILLER.sub(' ', re.sub(r'\s*(-|–|\bto\b|\buntil\b)\s*', ' ', TIME.sub(' ', text))).strip()
    if leftover or len(times) not in (1, 2):
        raise unclear(f'{kind} time "{words}" not understood')

    out = []
    for hour, minute, half in times:
        if half:
            if not 1 <= hour <= 12:
                raise unclear(f'{kind} time "{words}" has an impossible hour')
            hour = hour % 12 + (12 if half == 'p' else 0)
        if hour > 23 or minute > 59:
            raise unclear(f'{kind} time "{words}" has an impossible time')
        out.append((hour, minute))
    return zone, out


# ---- windows -----------------------------------------------------------------------------

def stop_window(stop, facility, received_at, kind, pickup_date=None, current_date=None):
    """(window_start, window_end) as aware datetimes in the facility's time zone."""
    local = ZoneInfo(facility['timezone'])
    received = received_at.astimezone(local).date()
    date_words, time_words = (stop.date, stop.time) if stop else (None, None)
    # The model sometimes puts the time in the date words ("Friday 9 AM-12 PM"): split at a clear time.
    clear_time = re.search(r'\b\d{1,2}(:\d{2})?\s*[ap]\.?m\b|\b\d{1,2}:\d{2}\b', date_words or '', re.IGNORECASE)
    if clear_time and not time_words:
        date_words, time_words = date_words[:clear_time.start()], date_words[clear_time.start():]
    day = parse_date(date_words, received, kind, pickup_date, current_date)
    times = parse_times(time_words, kind)

    if times is None:  # default hours that day
        open_, close = (time.fromisoformat(facility['default_hours'][k]) for k in ('open', 'close'))
        return datetime.combine(day, open_, local), datetime.combine(day, close, local)

    zone, points = times
    stated = ZoneInfo(zone) if zone else local
    start = datetime.combine(day, time(*points[0]), stated).astimezone(local)
    end = datetime.combine(day, time(*points[1]), stated).astimezone(local) if len(points) == 2 else start + timedelta(hours=1)
    if end <= start:
        raise unclear(f'{kind} time "{stop.time}" ends before it starts')
    return start, end


def load_windows(load, pickup_facility, delivery_facility, received_at, current=None):
    """Both windows for a load, as {'pickup': (start, end), 'delivery': (start, end)}.

    current is the booked load, for a change: a stop the change doesn't mention keeps its window,
    and a new time with no date keeps the stop's date. A new date with no time is unclear (keep the
    old times, or switch to default hours?).
    """
    windows = {}
    for kind, stop, facility in (('pickup', load.pickup, pickup_facility), ('delivery', load.delivery, delivery_facility)):
        booked = current and current[kind]
        if booked and not (stop and (stop.date or stop.time)):
            windows[kind] = tuple(datetime.fromisoformat(booked[k]) for k in ('window_start', 'window_end'))
            continue
        if booked and not stop.time:
            raise unclear(f'the change moves the {kind} to "{stop.date}" but gives no time')
        current_date = booked and datetime.fromisoformat(booked['window_start']).date()
        pickup_date = windows['pickup'][0].date() if 'pickup' in windows else None
        windows[kind] = stop_window(stop, facility, received_at, kind, pickup_date, current_date)
    if windows['delivery'][0] < windows['pickup'][0]:
        raise unclear('delivery starts before pickup')
    return windows
