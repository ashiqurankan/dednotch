"""Calendar events & reminders.

Reads the calendars GNOME already syncs (Evolution Data Server caches, which
include Google/Nextcloud/etc. accounts added in GNOME Online Accounts and the
GNOME Calendar local calendar) plus any extra .ics files configured by the
user.  Supports the common RRULE subset (DAILY/WEEKLY/MONTHLY/YEARLY with
INTERVAL, COUNT, UNTIL, BYDAY, BYMONTHDAY, EXDATE).
"""
import configparser
import datetime as dt
import glob
import os
import sqlite3
from dataclasses import dataclass, field

from gi.repository import GLib

from ..config import settings
from . import Observable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

EDS_CACHE = os.path.join(GLib.get_user_cache_dir(), "evolution", "calendar")
EDS_DATA = os.path.join(GLib.get_user_data_dir(), "evolution", "calendar")
EDS_TASKS_CACHE = os.path.join(GLib.get_user_cache_dir(), "evolution", "tasks")
EDS_TASKS_DATA = os.path.join(GLib.get_user_data_dir(), "evolution", "tasks")
EDS_SOURCES = os.path.join(GLib.get_user_config_dir(), "evolution", "sources")
LOCAL_TZ = dt.datetime.now().astimezone().tzinfo
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


@dataclass
class CalendarInfo:
    id: str
    title: str
    color: tuple


@dataclass
class Component:
    kind: str                      # "event" | "todo"
    uid: str
    summary: str
    location: str
    start: dt.datetime
    end: dt.datetime
    all_day: bool
    calendar: CalendarInfo
    rrule: dict = field(default_factory=dict)
    exdates: set = field(default_factory=set)
    completed: bool = False
    url: str = ""


@dataclass
class Event:
    id: str
    title: str
    location: str
    start: dt.datetime
    end: dt.datetime
    all_day: bool
    calendar: CalendarInfo
    is_reminder: bool = False
    completed: bool = False


# ---------------------------------------------------------------------------
# iCalendar parsing
# ---------------------------------------------------------------------------
def _unfold(text):
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split_prop(line):
    name_part, _, value = line.partition(":")
    pieces = name_part.split(";")
    params = {}
    for p in pieces[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return pieces[0].upper(), params, value


def _unescape(value):
    return (value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def _parse_dt(value, params):
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        d = dt.datetime.strptime(value[:8], "%Y%m%d")
        return d.replace(tzinfo=LOCAL_TZ), True
    utc = value.endswith("Z")
    base = dt.datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        return base.replace(tzinfo=dt.timezone.utc).astimezone(LOCAL_TZ), False
    tzid = params.get("TZID")
    if tzid and ZoneInfo is not None:
        try:
            return base.replace(tzinfo=ZoneInfo(tzid.split("/", 1)[-1] if tzid.startswith("/") else tzid)
                                ).astimezone(LOCAL_TZ), False
        except Exception:
            pass
    return base.replace(tzinfo=LOCAL_TZ), False


def _parse_duration(value):
    sign = -1 if value.startswith("-") else 1
    value = value.lstrip("+-").lstrip("P")
    days = secs = 0
    num = ""
    in_time = False
    for ch in value:
        if ch.isdigit():
            num += ch
            continue
        if ch == "T":
            in_time = True
            continue
        n = int(num or 0)
        num = ""
        if ch == "W":
            days += 7 * n
        elif ch == "D":
            days += n
        elif ch == "H" and in_time:
            secs += 3600 * n
        elif ch == "M" and in_time:
            secs += 60 * n
        elif ch == "S" and in_time:
            secs += n
    return sign * dt.timedelta(days=days, seconds=secs)


def parse_components(text, calendar):
    comps = []
    cur = None
    depth_alarm = 0
    for line in _unfold(text):
        if not line:
            continue
        if line.startswith("BEGIN:"):
            kind = line[6:].strip().upper()
            if kind in ("VEVENT", "VTODO") and cur is None:
                cur = {"_kind": kind, "EXDATE": []}
            elif cur is not None:
                depth_alarm += 1
            continue
        if line.startswith("END:"):
            kind = line[4:].strip().upper()
            if cur is not None and depth_alarm:
                depth_alarm -= 1
            elif cur is not None and kind == cur["_kind"]:
                comp = _build(cur, calendar)
                if comp:
                    comps.append(comp)
                cur = None
            continue
        if cur is None or depth_alarm:
            continue
        name, params, value = _split_prop(line)
        if name == "EXDATE":
            for v in value.split(","):
                try:
                    cur["EXDATE"].append(_parse_dt(v, params)[0])
                except ValueError:
                    pass
        elif name not in cur:
            cur[name] = (params, value)
    return comps


def _build(raw, calendar):
    try:
        kind = "event" if raw["_kind"] == "VEVENT" else "todo"
        status = raw.get("STATUS", ({}, ""))[1].upper()
        if kind == "event" and status == "CANCELLED":
            return None
        if "DTSTART" in raw:
            start, all_day = _parse_dt(raw["DTSTART"][1], raw["DTSTART"][0])
        elif kind == "todo" and "DUE" in raw:
            start, all_day = _parse_dt(raw["DUE"][1], raw["DUE"][0])
        else:
            return None
        if "DTEND" in raw:
            end = _parse_dt(raw["DTEND"][1], raw["DTEND"][0])[0]
        elif "DURATION" in raw:
            end = start + _parse_duration(raw["DURATION"][1])
        else:
            end = start + (dt.timedelta(days=1) if all_day else dt.timedelta(0))
        rrule = {}
        if "RRULE" in raw:
            for part in raw["RRULE"][1].split(";"):
                k, _, v = part.partition("=")
                rrule[k.upper()] = v
        return Component(
            kind=kind, uid=raw.get("UID", ({}, ""))[1],
            summary=_unescape(raw.get("SUMMARY", ({}, ""))[1]) or "(No title)",
            location=_unescape(raw.get("LOCATION", ({}, ""))[1]),
            start=start, end=end, all_day=all_day, calendar=calendar, rrule=rrule,
            exdates={d.replace(microsecond=0) for d in raw["EXDATE"]},
            completed=(status == "COMPLETED" or "COMPLETED" in raw),
            url=raw.get("URL", ({}, ""))[1])
    except (ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Recurrence
# ---------------------------------------------------------------------------
def _add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31,
                      30, 31, 31, 30, 31, 30, 31][m - 1])
    return d.replace(year=y, month=m, day=day)


def _nth_weekday(year, month, weekday, n):
    first = dt.date(year, month, 1)
    if n > 0:
        offset = (weekday - first.weekday()) % 7
        day = 1 + offset + (n - 1) * 7
    else:
        nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
        last = nxt - dt.timedelta(days=1)
        offset = (last.weekday() - weekday) % 7
        day = last.day - offset + (n + 1) * 7
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def occurrences(comp, range_start, range_end):
    """Yield start datetimes of `comp` overlapping [range_start, range_end)."""
    duration = comp.end - comp.start
    if not comp.rrule:
        if comp.start < range_end and (comp.end > range_start or comp.start >= range_start):
            yield comp.start
        return
    r = comp.rrule
    freq = r.get("FREQ", "")
    interval = max(1, int(r.get("INTERVAL", "1") or 1))
    count = int(r["COUNT"]) if "COUNT" in r else None
    until = None
    if "UNTIL" in r:
        try:
            until = _parse_dt(r["UNTIL"], {})[0]
            if comp.all_day:
                until = until + dt.timedelta(days=1)
        except ValueError:
            until = None
    byday = [b for b in r.get("BYDAY", "").split(",") if b]
    bymonthday = [int(x) for x in r.get("BYMONTHDAY", "").split(",") if x.lstrip("-").isdigit()]
    start = comp.start
    produced = 0

    def candidates_for_period(period_start):
        if freq == "WEEKLY" and byday:
            week_start = period_start - dt.timedelta(days=period_start.weekday())
            out = []
            for b in byday:
                code = b[-2:]
                if code in WEEKDAYS:
                    out.append(week_start + dt.timedelta(days=WEEKDAYS.index(code)))
            return sorted(out)
        if freq == "MONTHLY" and byday:
            out = []
            for b in byday:
                code, num = b[-2:], b[:-2]
                if code in WEEKDAYS and num.lstrip("+-").isdigit():
                    d = _nth_weekday(period_start.year, period_start.month, WEEKDAYS.index(code), int(num))
                    if d:
                        out.append(period_start.replace(year=d.year, month=d.month, day=d.day))
            return sorted(out)
        if freq == "MONTHLY" and bymonthday:
            out = []
            for md in bymonthday:
                try:
                    out.append(period_start.replace(day=md))
                except ValueError:
                    pass
            return sorted(out)
        return [period_start]

    def floor(period):
        if freq == "WEEKLY" and byday:
            return period - dt.timedelta(days=period.weekday())
        if freq == "MONTHLY" and (byday or bymonthday):
            return period.replace(day=1)
        return period

    def nth_period(n):
        if freq == "DAILY":
            return start + dt.timedelta(days=interval * n)
        if freq == "WEEKLY":
            return start + dt.timedelta(weeks=interval * n)
        if freq == "MONTHLY":
            base = start.replace(day=1) if (byday or bymonthday) else start
            return _add_months(base, interval * n)
        if freq == "YEARLY":
            try:
                return start.replace(year=start.year + interval * n)
            except ValueError:
                return start.replace(year=start.year + interval * n, day=28)
        return None

    n = 0
    period = start
    while period is not None and n < 20000:
        if floor(period) >= range_end:
            return
        for occ in candidates_for_period(period):
            if occ < start:
                continue
            if until is not None and occ > until:
                return
            if count is not None and produced >= count:
                return
            produced += 1
            if occ >= range_end:
                return
            if occ.replace(microsecond=0) in comp.exdates:
                continue
            if occ + duration > range_start or occ >= range_start:
                yield occ
        n += 1
        period = nth_period(n)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
def _registry_sources():
    """uid -> keyfile text, from the running Evolution source registry (covers
    GNOME Online Accounts calendars, which have no .source file on disk)."""
    out = {}
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "ListNames", None, None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        reg = [n for n in names if n.startswith("org.gnome.evolution.dataserver.Sources")]
        if not reg:
            return out
        objs = bus.call_sync(reg[0], "/org/gnome/evolution/dataserver/SourceManager",
                             "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None, None,
                             Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        for ifaces in objs.values():
            src = ifaces.get("org.gnome.evolution.dataserver.Source", {})
            if src.get("UID"):
                out[src["UID"]] = src.get("Data", "")
    except Exception:
        pass
    return out


_REGISTRY = {}


def _source_info(uid):
    path = os.path.join(EDS_SOURCES, uid + ".source")
    title, color = uid[:8], None
    if uid in _REGISTRY:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read_string(_REGISTRY[uid])
            title = cp.get("Data Source", "DisplayName", fallback=title) or title
            for section in ("Calendar", "Task List"):
                if cp.has_section(section):
                    color = cp.get(section, "Color", fallback=None) or color
            return title, color
        except configparser.Error:
            pass
    if os.path.exists(path):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(path, encoding="utf-8")
            title = cp.get("Data Source", "DisplayName", fallback=title)
            for section in ("Calendar", "Task List"):
                if cp.has_section(section):
                    color = cp.get(section, "Color", fallback=None) or color
            parent = cp.get("Data Source", "Parent", fallback="")
            if title in ("", uid[:8]) and parent:
                title = parent
        except configparser.Error:
            pass
    return title, color


def _hex(color, fallback=(0.04, 0.52, 1.0)):
    try:
        c = color.lstrip("#")
        if len(c) == 12:   # #rrrrggggbbbb
            return tuple(int(c[i:i + 4], 16) / 65535.0 for i in (0, 4, 8))
        return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except Exception:
        return fallback


class CalendarService(Observable):
    def __init__(self):
        super().__init__()
        self.calendars = []
        self.components = []
        self._day_cache = {}
        self.reload()
        GLib.timeout_add_seconds(300, self._periodic)
        settings.connect(self._on_setting)

    def _on_setting(self, key, _v):
        if key in ("disabled_calendars", "extra_ics_files"):
            self.reload()

    def _periodic(self):
        self.reload()
        return True

    def reload(self):
        calendars, comps = [], []
        disabled = set(settings["disabled_calendars"])
        _REGISTRY.clear()
        _REGISTRY.update(_registry_sources())
        sources = []
        for base in (EDS_CACHE, EDS_DATA, EDS_TASKS_CACHE, EDS_TASKS_DATA):
            for path in glob.glob(os.path.join(base, "*")):
                uid = os.path.basename(path)
                if uid == "trash":
                    continue
                sources.append((uid, path))
        seen = set()
        for uid, path in sources:
            if uid in seen:
                continue
            title, color = _source_info("system-calendar" if uid == "system" else uid)
            texts = []
            db = os.path.join(path, "cache.db")
            if os.path.exists(db):
                try:
                    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=1)
                    texts = [row[0] for row in con.execute("SELECT ECacheOBJ FROM ECacheObjects") if row[0]]
                    con.close()
                except sqlite3.Error:
                    texts = []
            for ics in glob.glob(os.path.join(path, "*.ics")):
                try:
                    with open(ics, encoding="utf-8", errors="replace") as fh:
                        texts.append(fh.read())
                except OSError:
                    pass
            if not texts:
                continue
            seen.add(uid)
            cal = CalendarInfo(uid, title, _hex(color) if color else settings.accent())
            calendars.append(cal)
            if uid in disabled:
                continue
            for t in texts:
                comps.extend(parse_components(t, cal))
        for ics in settings["extra_ics_files"]:
            try:
                with open(os.path.expanduser(ics), encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            cal = CalendarInfo(ics, os.path.basename(ics), settings.accent())
            calendars.append(cal)
            if ics not in disabled:
                comps.extend(parse_components(text, cal))
        self.calendars = calendars
        self.components = comps
        self._day_cache.clear()
        self.emit("changed")

    def events_for(self, date):
        """Events and reminders for a calendar day, sorted like EventKit."""
        key = date.isoformat() if hasattr(date, "isoformat") else str(date)
        if key in self._day_cache:
            return self._day_cache[key]
        start = dt.datetime(date.year, date.month, date.day, tzinfo=LOCAL_TZ)
        end = start + dt.timedelta(days=1)
        out = []
        for comp in self.components:
            try:
                for occ in occurrences(comp, start, end):
                    occ_end = occ + (comp.end - comp.start)
                    out.append(Event(id="%s@%s" % (comp.uid, occ.isoformat()), title=comp.summary,
                                     location=comp.location, start=occ, end=occ_end,
                                     all_day=comp.all_day, calendar=comp.calendar,
                                     is_reminder=comp.kind == "todo", completed=comp.completed))
            except Exception:
                continue
        out.sort(key=lambda e: (not e.all_day, e.start, e.title))
        self._day_cache[key] = out
        return out
