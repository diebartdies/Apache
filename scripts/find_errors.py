"""
find_errors.py
Reads D:\app.txt, finds lines containing the word "error" (case-insensitive),
and reports any dates found on those lines.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

# ── Date patterns (most specific first) ──────────────────────────────────────
DATE_PATTERNS = [
    # ISO datetime:  2024-01-15T12:30:45  /  2024-01-15 12:30:45
    (r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b",
     "ISO datetime"),
    # ISO date only: 2024-01-15
    (r"\b(\d{4}-\d{2}-\d{2})\b", "ISO date"),
    # US / EU slash: 01/15/2024  or  15/01/2024
    (r"\b(\d{1,2}/\d{1,2}/\d{4})\b", "date (dd/mm or mm/dd)"),
    # US slash short: 01/15/24
    (r"\b(\d{1,2}/\d{1,2}/\d{2})\b", "date (short year)"),
    # Month-name long: January 15, 2024  /  Jan 15 2024
    (r"\b((?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
     r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
     r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})\b",
     "named-month date"),
    # Compact: 20240115
    (r"\b(20\d{6})\b", "compact date (YYYYMMDD)"),
]


def find_dates(text: str) -> list[tuple[str, str]]:
    """Return a list of (matched_date_string, pattern_name) found in *text*."""
    found = []
    seen = set()
    for pattern, name in DATE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            val = m.group(1)
            if val not in seen:
                seen.add(val)
                found.append((val, name))
    return found


def main():
    log_file = Path(r"D:\app.txt")
    output_file = Path(r"D:\py_err_out")

    if not log_file.exists():
        print(f"[ERROR] File not found: {log_file}", file=sys.stderr)
        sys.exit(1)

    report_lines: list[str] = []
    report_lines.append(f"Scanning: {log_file}")
    report_lines.append("-" * 60)

    error_lines: list[tuple[int, str, list]] = []

    with log_file.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            if "error" in line.lower():
                dates = find_dates(line)
                error_lines.append((line_no, line.rstrip(), dates))

    if not error_lines:
        report_lines.append("No lines containing 'error' were found.")
        output_file.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"Output written to: {output_file}")
        return

    # ── Count errors per date ─────────────────────────────────────────────
    date_counts: dict[str, int] = defaultdict(int)
    no_date_count = 0

    for _, _, dates in error_lines:
        if dates:
            for date_val, _ in dates:
                date_counts[date_val] += 1
        else:
            no_date_count += 1

    sorted_dates = sorted(date_counts.keys())

    report_lines.append(f"Total error lines : {len(error_lines)}")
    report_lines.append(f"Lines without date: {no_date_count}")
    report_lines.append("")

    if sorted_dates:
        col_w = max(len(d) for d in sorted_dates)
        report_lines.append(f"{'Date':<{col_w}}   Errors")
        report_lines.append(f"{'-' * col_w}   {'-' * 6}")
        for d in sorted_dates:
            report_lines.append(f"{d:<{col_w}}   {date_counts[d]:>6}")
        report_lines.append(f"{'-' * col_w}   {'-' * 6}")
        report_lines.append(f"{'TOTAL':<{col_w}}   {sum(date_counts.values()):>6}")
    else:
        report_lines.append("No dates detected on any error line.")

    output_file.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"Output written to: {output_file}")


if __name__ == "__main__":
    main()
