"""Read-only archive of the original Excel workbook (reference/BTC_Dashboard.xlsx).

Loaded into MongoDB collection `workbook_archive` once, in the background, the first time the
server starts with an empty archive. Rows of the "__bpstmp_*" backup sheets are kept only when
they do not already exist in the matching primary sheet ("backup-unique"). Never used as live data.
"""
import asyncio
import logging
import os
from datetime import date, datetime, time

from core import db

log = logging.getLogger("workbook_archive")
BACKUP_PREFIX = "__bpstmp_"


def _cell(v):
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, float) and v != v:
        return None
    return v


def read_rows(path):
    import warnings
    from openpyxl import load_workbook
    warnings.filterwarnings("ignore", module="openpyxl")
    wb = load_workbook(path, read_only=True, data_only=True)
    sheets = {}
    for ws in wb.worksheets:
        rows = []
        for i, r in enumerate(ws.iter_rows(values_only=True), 1):
            vals = [_cell(v) for v in r]
            while vals and vals[-1] in (None, ""):
                vals.pop()
            if vals:
                rows.append((i, vals))
        sheets[ws.title] = rows
    wb.close()
    import json
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbook_audit.json")) as fh:
            backup_of = {s["backup"]: s["sheet"] for s in json.load(fh)["sheets"] if s.get("backup")}
    except Exception:
        backup_of = {}
    out = []
    for name, rows in sheets.items():
        if name.startswith(BACKUP_PREFIX):
            base = backup_of.get(name, name)
            seen = {tuple(map(str, v)) for _, v in sheets.get(base, [])}
            out += [{"sheet": base, "source": "backup-unique", "row": i, "values": v} for i, v in rows if tuple(map(str, v)) not in seen]
        else:
            out += [{"sheet": name, "source": "primary", "row": i, "values": v} for i, v in rows]
    return out


async def ensure_loaded():
    path = os.environ.get("WORKBOOK_PATH") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reference", "BTC_Dashboard.xlsx")
    try:
        if not os.path.exists(path) or await db.workbook_archive.estimated_document_count():
            return
        docs = await asyncio.to_thread(read_rows, path)
        for i in range(0, len(docs), 1000):
            await db.workbook_archive.insert_many(docs[i:i + 1000], ordered=False)
        await db.workbook_archive.create_index([("sheet", 1), ("source", 1), ("row", 1)])
        log.info(f"workbook archive loaded: {len(docs)} rows")
    except Exception:
        log.exception("workbook archive load failed (reference page only; live desk unaffected)")
