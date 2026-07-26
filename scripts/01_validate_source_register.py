#!/usr/bin/env python3
"""Validate the project data-source register and create a readable summary."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTER = PROJECT_ROOT / "docs" / "data_sources.csv"
SUMMARY = PROJECT_ROOT / "docs" / "data_sources.md"
REQUIRED_COLUMNS = {
    "source_id",
    "dataset_name",
    "provider",
    "download_url",
    "access_date",
    "licence",
    "source_crs",
    "local_file",
    "purpose",
    "limitations",
    "status",
}


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    with REGISTER.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing_columns = REQUIRED_COLUMNS - columns
        if missing_columns:
            raise ValueError(
                f"The source register is missing columns: {', '.join(sorted(missing_columns))}"
            )
        rows = list(reader)

    problems: list[str] = []
    for row in rows:
        source_id = row["source_id"].strip()
        if not source_id:
            problems.append("A row has no source_id.")
            continue
        if not row["download_url"].strip():
            problems.append(f"{source_id}: no official download URL recorded.")
        if not row["local_file"].strip():
            problems.append(f"{source_id}: no expected local file recorded.")

    lines = [
        "# Data source register",
        "",
        f"Generated: {date.today().isoformat()} by `scripts/01_validate_source_register.py`.",
        "",
        "This is the audit trail for spatial inputs used by the bog-restoration workflow. "
        "Files remain `planned` until an original source file is downloaded and its access date is recorded.",
        "",
        "| ID | Dataset | Provider | Local file | Status | Purpose |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(row[key])
                for key in ("source_id", "dataset_name", "provider", "local_file", "status", "purpose")
            )
            + " |"
        )

    lines.extend(["", "## Validation", ""])
    if problems:
        lines.extend(f"- {problem}" for problem in problems)
    else:
        lines.append("- Register structure is complete.")

    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Validated {len(rows)} source-register entries.")
    print(f"Wrote {SUMMARY.relative_to(PROJECT_ROOT)}")
    if problems:
        print("Issues found:")
        for problem in problems:
            print(f"- {problem}")


if __name__ == "__main__":
    main()
