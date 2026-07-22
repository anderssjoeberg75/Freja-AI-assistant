"""Export a generated training plan out of F.R.E.J.A. (Issue #39).

Two formats, both produced without any third-party dependency so the export works on a
bare install:

  * ``build_ics``  - an RFC 5545 calendar any calendar app can import.
  * ``build_pdf``  - a minimal, printable PDF written by hand (see ``_PdfWriter``).

Plans are stored as the raw JSON text Gemini returned (``trainer_plans.advice_text``), so
``parse_plan_text`` is the single place that tolerates the ```json fences the model
sometimes wraps around it. Plan content is Swedish - it is what the user reads - so every
label rendered into an export stays Swedish too.
"""

import datetime
import textwrap

# Weekday name -> offset from the plan's start date. The generate schema forces Swedish
# weekday names, and `book_plan_to_calendar` parses them back the same way; keeping the
# single mapping here stops the calendar booking and the export from drifting apart.
SWEDISH_DAY_OFFSETS = {
    "måndag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3,
    "fredag": 4, "lördag": 5, "söndag": 6,
}

DEFAULT_EXPORT_HOUR = 8      # Start hour used for exported sessions
MAX_EXPORT_MINUTES = 180     # Same sanity cap the calendar booking applies


def parse_plan_text(advice_text: str):
    """Parses a stored plan's advice_text into a dict, or returns None if it is free text.

    Strips the ```json / ``` fences Gemini occasionally emits around structured output."""
    import json

    if not advice_text:
        return None
    text = str(advice_text).strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    try:
        data = json.loads(text.strip())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def plan_occurrences(plan_data: dict, start_date: datetime.date) -> list:
    """Turns a plan's workouts into concrete dated sessions starting from start_date.

    Mirrors the scheduling `book_plan_to_calendar` performs (weekday offset + optional
    `week` for multi-week plans, duration capped), minus the calendar-conflict search -
    an export has no calendar to check against, so sessions start at DEFAULT_EXPORT_HOUR.
    Rest days (duration 0) and unknown weekday names are skipped."""
    occurrences = []
    for w in (plan_data.get("workouts") or []):
        if not isinstance(w, dict):
            continue
        offset = SWEDISH_DAY_OFFSETS.get(str(w.get("day", "")).strip().lower())
        if offset is None:
            continue
        try:
            duration = int(w.get("duration_minutes", 0) or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            continue  # rest day
        duration = min(duration, MAX_EXPORT_MINUTES)
        try:
            week = max(0, min(51, int(w.get("week", 0) or 0)))
        except (TypeError, ValueError):
            week = 0

        day = start_date + datetime.timedelta(days=offset + week * 7)
        start_dt = datetime.datetime.combine(day, datetime.time(DEFAULT_EXPORT_HOUR, 0))
        occurrences.append({
            "workout": w,
            "date": day,
            "start": start_dt,
            "end": start_dt + datetime.timedelta(minutes=duration),
            "duration": duration,
        })
    occurrences.sort(key=lambda o: o["start"])
    return occurrences


def format_exercises(exercises) -> list:
    """Renders a workout's structured exercises as plain "namn: 3x8 @ 60 kg" lines."""
    lines = []
    for ex in (exercises or []):
        if not isinstance(ex, dict):
            continue
        name = str(ex.get("name") or "").strip()
        if not name:
            continue

        def _num(key):
            try:
                return float(ex.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        sets, reps = int(_num("sets")), int(_num("reps"))
        weight, rpe = _num("target_weight"), _num("rpe")
        detail = f"{sets}x{reps}" if (sets or reps) else ""
        if weight > 0:
            detail += f" @ {weight:g} kg"
        elif rpe > 0:
            detail += f" @ RPE {rpe:g}"
        lines.append(f"{name}: {detail}".strip().rstrip(":"))
    return lines


# --- ICS ---------------------------------------------------------------------

def _ics_escape(value: str) -> str:
    """Escapes a value for an iCalendar TEXT property (RFC 5545 §3.3.11)."""
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _ics_fold(line: str) -> str:
    """Folds a content line to the 75-octet limit, continuing with a leading space.

    Folding is counted in octets, not characters, because Swedish letters are two bytes
    in UTF-8 and an over-long line makes strict parsers reject the file."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks, current, size = [], [], 0
    limit = 75
    for ch in line:
        ch_size = len(ch.encode("utf-8"))
        if size + ch_size > limit:
            chunks.append("".join(current))
            current, size, limit = [ch], ch_size, 74  # continuation lines carry a leading space
        else:
            current.append(ch)
            size += ch_size
    chunks.append("".join(current))
    return "\r\n ".join(chunks)


def build_ics(plan: dict, plan_data: dict, start_date: datetime.date, now: datetime.datetime = None) -> str:
    """Builds an RFC 5545 calendar for a plan's workouts.

    Times are floating (no timezone), which is what a personal training plan means: the
    08:00 session is 08:00 wherever the user opens the calendar."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    plan_id = plan.get("id")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//F.R.E.J.A.//COACH AI//SV",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape('Träningsplan: ' + str(plan.get('goal') or 'COACH AI'))}",
    ]

    for idx, occ in enumerate(plan_occurrences(plan_data, start_date)):
        w = occ["workout"]
        summary = f"💪 {w.get('activity_type') or 'Träning'}: {w.get('title') or 'Pass'}"
        desc_parts = [str(w.get("description") or "").strip()]
        exercises = format_exercises(w.get("exercises"))
        if exercises:
            desc_parts.append("Övningar:\n" + "\n".join(f"- {e}" for e in exercises))
        desc_parts.append(f"Tid: {occ['duration']} minuter.")
        desc_parts.append("Genererat av F.R.E.J.A. COACH AI.")
        description = "\n\n".join(p for p in desc_parts if p)

        lines += [
            "BEGIN:VEVENT",
            f"UID:freja-plan-{plan_id}-{idx}@freja.local",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{occ['start'].strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{occ['end'].strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "LOCATION:F.R.E.J.A. PT",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(line) for line in lines) + "\r\n"


# --- PDF ---------------------------------------------------------------------

_PAGE_WIDTH, _PAGE_HEIGHT = 595, 842   # A4 in PostScript points
_MARGIN = 56
_LEADING = 15

# style -> (pdf font resource, size, rgb). Helvetica metrics average ~0.5em per glyph,
# which is what the wrap width below assumes.
_STYLES = {
    "h1":     ("F2", 18, (0.00, 0.30, 0.45)),
    "h2":     ("F2", 12, (0.00, 0.30, 0.45)),
    "body":   ("F1", 10, (0.10, 0.10, 0.10)),
    "muted":  ("F3", 9,  (0.40, 0.40, 0.40)),
    "spacer": ("F1", 10, (0, 0, 0)),
}


def _pdf_escape(text: str) -> bytes:
    """Encodes a string as a PDF literal string body in WinAnsi (covers åäöé)."""
    raw = str(text).encode("cp1252", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _layout_lines(blocks: list) -> list:
    """Wraps (style, text) blocks to the page width, returning one entry per printed line."""
    usable = _PAGE_WIDTH - 2 * _MARGIN
    out = []
    for style, text in blocks:
        if style == "spacer":
            out.append(("spacer", ""))
            continue
        _, size, _ = _STYLES.get(style, _STYLES["body"])
        max_chars = max(20, int(usable / (size * 0.5)))
        for paragraph in str(text or "").split("\n"):
            wrapped = textwrap.wrap(paragraph, max_chars) or [""]
            for line in wrapped:
                out.append((style, line))
    return out


def _content_stream(lines: list) -> bytes:
    """Renders one page's worth of laid-out lines into a PDF content stream."""
    parts = []
    y = _PAGE_HEIGHT - _MARGIN
    for style, text in lines:
        if style == "spacer":
            y -= _LEADING // 2
            continue
        font, size, (r, g, b) = _STYLES.get(style, _STYLES["body"])
        parts.append(
            f"{r:.2f} {g:.2f} {b:.2f} rg BT /{font} {size} Tf {_MARGIN} {y} Td ".encode("ascii")
            + b"(" + _pdf_escape(text) + b") Tj ET\n"
        )
        y -= _LEADING
    return b"".join(parts)


def _build_pdf_document(pages: list) -> bytes:
    """Assembles page content streams into a PDF 1.4 file with a valid xref table."""
    objects = []   # 1-indexed object bodies

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog_num, pages_num = 1, 2
    objects.append(b"")  # placeholder for the catalog
    objects.append(b"")  # placeholder for the page tree
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    f3 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>")
    resources = (
        f"<< /Font << /F1 {f1} 0 R /F2 {f2} 0 R /F3 {f3} 0 R >> >>".encode("ascii")
    )

    page_nums = []
    for content in pages:
        stream_num = add(b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"endstream")
        page_nums.append(add(
            f"<< /Type /Page /Parent {pages_num} 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
            f"/Contents {stream_num} 0 R /Resources ".encode("ascii") + resources + b" >>"
        ))

    kids = b" ".join(f"{n} 0 R".encode("ascii") for n in page_nums)
    objects[pages_num - 1] = b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_nums)).encode("ascii") + b" >>"
    objects[catalog_num - 1] = f"<< /Type /Catalog /Pages {pages_num} 0 R >>".encode("ascii")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_num} 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def build_pdf(plan: dict, plan_data, start_date: datetime.date = None) -> bytes:
    """Builds a printable PDF of a training plan.

    Falls back to the raw advice text when the plan has no structured JSON (older plans),
    so every stored plan is exportable."""
    goal = str(plan.get("goal") or "Träningsplan")
    blocks = [
        ("h1", "F.R.E.J.A. – Träningsplan"),
        ("muted", f"Mål: {goal}   •   Skapad: {plan.get('date') or '-'}"),
        ("spacer", ""),
    ]

    if not isinstance(plan_data, dict):
        blocks.append(("body", str(plan.get("advice_text") or "Ingen plan sparad.")))
    else:
        if plan_data.get("weekly_focus"):
            blocks += [("h2", "Veckans fokus"), ("body", plan_data["weekly_focus"]), ("spacer", "")]
        if plan_data.get("summary"):
            blocks += [("h2", "Sammanfattning"), ("body", plan_data["summary"]), ("spacer", "")]
        for label, key in (("Vilopuls (RHR)", "resting_hr_trend"), ("HRV", "hrv_trend")):
            if plan_data.get(key):
                blocks.append(("body", f"{label}: {plan_data[key]}"))
        blocks.append(("spacer", ""))

        blocks.append(("h2", "Pass"))
        # When a start date is given, label each session with the date it falls on. Keyed by
        # (day, week) - not day alone - since a multi-week plan repeats weekday names across
        # weeks, and a day-only key would collapse every week onto the first occurrence's date.
        dates_by_day = {}
        if start_date:
            for occ in plan_occurrences(plan_data, start_date):
                key = (str(occ["workout"].get("day", "")).lower(), occ["workout"].get("week", 0) or 0)
                dates_by_day.setdefault(key, occ["date"])

        for w in (plan_data.get("workouts") or []):
            if not isinstance(w, dict):
                continue
            when = dates_by_day.get((str(w.get("day", "")).lower(), w.get("week", 0) or 0))
            day_label = str(w.get("day", "")) + (f" ({when.isoformat()})" if when else "")
            try:
                duration = int(w.get("duration_minutes") or 0)
            except (TypeError, ValueError):
                duration = 0
            if duration > 0:
                heading = (
                    f"{day_label} – {w.get('activity_type') or 'Träning'}: "
                    f"{w.get('title') or ''} [{duration} min]"
                )
            else:
                heading = f"{day_label} – Vila"
            blocks.append(("h2", heading))
            if w.get("description"):
                blocks.append(("body", str(w["description"])))
            for ex_line in format_exercises(w.get("exercises")):
                blocks.append(("body", f"   • {ex_line}"))
            blocks.append(("spacer", ""))

    blocks += [("spacer", ""), ("muted", "Genererad av F.R.E.J.A. COACH AI.")]

    # Paginate: how many lines fit between the top and bottom margins.
    lines = _layout_lines(blocks)
    per_page = max(1, int((_PAGE_HEIGHT - 2 * _MARGIN) / _LEADING))
    pages = [
        _content_stream(lines[i:i + per_page])
        for i in range(0, len(lines), per_page)
    ] or [_content_stream([])]
    return _build_pdf_document(pages)
