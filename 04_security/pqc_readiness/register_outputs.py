"""Write the migration register as CSV (for tooling) and Excel (for people).

FORMULA INJECTION: file paths and matched text come from the scanned folder, so a file
named "=HYPERLINK(...).conf" would otherwise become a live formula when the register is
opened in a spreadsheet. In the CSV, any value starting with = + - @ or a tab or carriage
return gets a leading apostrophe (the usual spreadsheet-safe convention); in the xlsx,
every such value is stored as a text cell, never a formula.

migration_register.xlsx tabs:
    Register       one row per finding, header frozen, filters on every column
    Summary        counts by priority, by priority x governing data type, and by rule
    Priority rule  the rule list, so any row's priority can be checked by hand
    Evidence       tool version, input hashes, scan time, and how to reproduce the rows
"""

from __future__ import annotations

import csv
import tempfile
from collections import Counter
from dataclasses import astuple, replace
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common import Check
from config import Config
from asset_register import UNCLASSIFIED
from migration_register import COLUMNS, RegisterRow, RunMeta

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    text = str(value)
    return "'" + text if text.startswith(FORMULA_START) else text

HEADER_FILL = PatternFill("solid", fgColor="E1E0D9")
WIDTHS = {"priority": 18, "priority_reason": 44, "asset_names": 30, "file": 34,
          "detected": 34, "matched_text": 22, "classification": 44, "remediation": 70,
          "remediation_sources": 40, "file_sha256": 20, "config_sha256": 20,
          "register_sha256": 20}


def write_csv(path: Path, rows: list[RegisterRow]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows([csv_safe(v) for v in astuple(r)] for r in rows)


def _header(ws, names) -> None:
    ws.append(list(names))
    for cell in ws[ws.max_row]:
        cell.font, cell.fill = Font(bold=True), HEADER_FILL


def _register_sheet(ws, rows: list[RegisterRow]) -> None:
    ws.title = "Register"
    _header(ws, COLUMNS)
    for r in rows:
        ws.append(list(astuple(r)))
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str) and cell.value.startswith(FORMULA_START):
                cell.data_type = "s"          # text, never a formula
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(rows) + 1}"
    for i, name in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(name, 14)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _summary_sheet(ws, rows: list[RegisterRow], cfg: Config) -> None:
    by_priority = Counter(r.priority for r in rows)
    ws.append(["Rows by priority"])
    ws["A1"].font = Font(bold=True)
    _header(ws, ["Priority", "Rows"])
    for level in cfg.priority_levels:
        ws.append([level, by_priority.get(level, 0)])
    ws.append(["Total", len(rows)])
    types = [d.name for d in cfg.data_types] + [UNCLASSIFIED]
    cross = Counter((r.priority, r.governing_data_type) for r in rows)
    ws.append([])
    ws.append(["Rows by priority and governing data type (longest shelf life on the file)"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    _header(ws, ["Priority"] + types)
    for level in cfg.priority_levels:
        ws.append([level] + [cross.get((level, t), 0) for t in types])
    ws.append([])
    ws.append(["Rows by rule"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    _header(ws, ["Rule", "Priority", "Rows"])
    by_rule = Counter(r.priority_rule for r in rows)
    for rule in cfg.priority_rules:
        ws.append([rule.rule_id, rule.priority, by_rule.get(rule.rule_id, 0)])
    ws.column_dimensions["A"].width = 22
    for i in range(2, len(types) + 2):
        ws.column_dimensions[get_column_letter(i)].width = 16


def _rule_sheet(ws, cfg: Config) -> None:
    _header(ws, ["Order", "Rule", "Priority", "Applies when", "Reason"])
    for i, rule in enumerate(cfg.priority_rules, start=1):
        ws.append([i, rule.rule_id, rule.priority, rule.describe(), rule.reason])
    ws.append([])
    ws.append(["The FIRST rule whose conditions all hold decides a row. The rules are a "
               "planning aid, not a risk rating from a certified assessment."])
    for col, width in zip("ABCDE", (7, 7, 18, 60, 80)):
        ws.column_dimensions[col].width = width


def _evidence_sheet(ws, meta: RunMeta, cfg: Config, register_label: str) -> None:
    _header(ws, ["Item", "Value"])
    for item in (("Tool version", meta.tool_version), ("Scan timestamp (UTC)", meta.scan_timestamp),
                 ("Scanned folder", f"{meta.scan_root}/"),
                 (f"SHA-256 of {cfg.path_label}", meta.config_sha256),
                 (f"SHA-256 of {register_label}", meta.register_sha256),
                 ("Reproduce", "Run `py run_all.py` with the same config, register and "
                               "folder: every row except the timestamp must match, and "
                               "each file_sha256 must equal a fresh SHA-256 of that file.")):
        ws.append(list(item))
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 30, 90


def write_xlsx(path: Path, rows: list[RegisterRow], cfg: Config, meta: RunMeta,
               register_label: str) -> None:
    wb = Workbook()
    wb.properties.creator = "pqc-readiness-toolkit"      # no user name in the metadata
    _register_sheet(wb.active, rows)
    _summary_sheet(wb.create_sheet("Summary"), rows, cfg)
    _rule_sheet(wb.create_sheet("Priority rule"), cfg)
    _evidence_sheet(wb.create_sheet("Evidence"), meta, cfg, register_label)
    wb.save(path)


def output_checks(rows: list[RegisterRow], csv_path: Path, xlsx_path: Path,
                  cfg: Config) -> list[Check]:
    """Read both files back and require them to hold exactly the register rows."""
    with csv_path.open(newline="", encoding="utf-8") as handle:
        back = list(csv.reader(handle))
    want = [list(COLUMNS)] + [[csv_safe(v) for v in astuple(r)] for r in rows]
    want_xlsx = [list(COLUMNS)] + [[str(v) for v in astuple(r)] for r in rows]
    wb = load_workbook(xlsx_path, read_only=True)
    sheet = [[("" if v is None else str(v)) for v in row]
             for row in wb["Register"].iter_rows(values_only=True)]
    summary = {row[0]: row[1] for row in wb["Summary"].iter_rows(min_row=3, max_row=3
                                                                  + len(cfg.priority_levels),
                                                                  values_only=True)}
    sheets = wb.sheetnames
    wb.close()
    counts = Counter(r.priority for r in rows)
    return [
        Check("Outputs: migration_register.csv reads back as exactly the register rows",
              back == want, f"{len(back) - 1} rows"),
        Check("Outputs: the xlsx Register tab holds the same rows, with all four tabs",
              sheet == want_xlsx and sheets == ["Register", "Summary", "Priority rule", "Evidence"],
              f"{len(sheet) - 1} rows; tabs {sheets}"),
        Check("Outputs: the xlsx Summary counts equal the register's counts by priority",
              all(summary.get(level) == counts.get(level, 0) for level in cfg.priority_levels)
              and summary.get("Total") == len(rows)),
        _injection_check(rows, cfg),
    ]


def _injection_check(rows: list[RegisterRow], cfg: Config) -> Check:
    """Write a row whose file name and matched text are formulas; neither output may keep
    them as live formulas."""
    evil = replace(rows[0], file='=HYPERLINK("http://example.invalid","x").conf',
                   matched_text="@SUM(1+1)")
    meta = RunMeta("t", "v", "0" * 64, "0" * 64, "x")
    with tempfile.TemporaryDirectory() as scratch:
        csv_path, xlsx_path = Path(scratch) / "r.csv", Path(scratch) / "r.xlsx"
        write_csv(csv_path, [evil])
        write_xlsx(xlsx_path, [evil], cfg, meta, "register")
        with csv_path.open(newline="", encoding="utf-8") as handle:
            cells = list(csv.reader(handle))[1]
        wb = load_workbook(xlsx_path)
        row = list(wb["Register"].iter_rows(min_row=2, max_row=2))[0]
        formulas = [c.coordinate for c in row if c.data_type == "f"]
        wb.close()
    file_cell = cells[COLUMNS.index("file")]
    match_cell = cells[COLUMNS.index("matched_text")]
    return Check("Outputs: formula-like values cannot run in the CSV or the xlsx",
                 not formulas and file_cell.startswith("'=") and match_cell.startswith("'@"),
                 f"xlsx formula cells: {formulas or 'none'}")
