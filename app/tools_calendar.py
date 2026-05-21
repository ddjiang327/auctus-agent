"""macOS Calendar integration via AppleScript (osascript).

Non-macOS systems get a clear error message.
"""
from __future__ import annotations

import platform
import subprocess
from datetime import datetime, timedelta


def _osascript(script: str) -> tuple[str, str]:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    return r.stdout.strip(), r.stderr.strip()


def _as_date(dt: datetime) -> str:
    return dt.strftime("%-m/%-d/%Y %H:%M:%S")


def _as_applescript_string(value: str) -> str:
    """Return an AppleScript expression for a literal string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def list_calendar_events(days_ahead: int = 7, calendar_name: str = "") -> dict:
    """List upcoming events from macOS Calendar.app."""
    if platform.system() != "Darwin":
        return {"error": "Calendar integration requires macOS"}

    days_ahead = max(1, min(int(days_ahead), 90))
    cal_filter = f"whose name is {_as_applescript_string(calendar_name.strip())}" if calendar_name.strip() else ""

    script = f"""
set out to ""
tell application "Calendar"
    set startDate to current date
    set endDate to startDate + {days_ahead} * days
    repeat with cal in (calendars {cal_filter})
        set cname to name of cal
        set evts to (events of cal whose start date >= startDate and start date <= endDate)
        repeat with ev in evts
            set out to out & cname & "|||" & summary of ev & "|||" & (start date of ev as string) & "|||" & (end date of ev as string) & "###"
        end repeat
    end repeat
end tell
return out
"""
    stdout, stderr = _osascript(script)
    if stderr and not stdout:
        return {"error": stderr}

    events = []
    for chunk in stdout.split("###"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("|||")
        if len(parts) >= 4:
            events.append({"calendar": parts[0], "title": parts[1], "start": parts[2], "end": parts[3]})

    events.sort(key=lambda e: e.get("start", ""))
    return {"events": events, "count": len(events)}


def create_calendar_event(
    title: str,
    start_datetime: str,
    end_datetime: str = "",
    notes: str = "",
    calendar_name: str = "",
) -> dict:
    """Create a Calendar.app event. Datetime format: 'YYYY-MM-DD HH:MM'."""
    if platform.system() != "Darwin":
        return {"error": "Calendar integration requires macOS"}

    try:
        start_dt = datetime.strptime(start_datetime.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return {"error": "start_datetime must be 'YYYY-MM-DD HH:MM'"}

    if end_datetime.strip():
        try:
            end_dt = datetime.strptime(end_datetime.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            return {"error": "end_datetime must be 'YYYY-MM-DD HH:MM'"}
    else:
        end_dt = start_dt + timedelta(hours=1)

    cal_target = f"calendar {_as_applescript_string(calendar_name.strip())}" if calendar_name.strip() else "default calendar"
    notes_line = f"set description of newEv to {_as_applescript_string(notes)}" if notes else ""

    script = f"""
tell application "Calendar"
    tell {cal_target}
        set newEv to make new event with properties {{summary:{_as_applescript_string(title)}, start date:date "{_as_date(start_dt)}", end date:date "{_as_date(end_dt)}"}}
        {notes_line}
    end tell
    reload calendars
end tell
return "ok"
"""
    stdout, stderr = _osascript(script)
    if "ok" not in stdout and stderr:
        return {"error": f"Failed to create event: {stderr}"}
    return {"ok": True, "title": title, "start": str(start_dt), "end": str(end_dt)}
