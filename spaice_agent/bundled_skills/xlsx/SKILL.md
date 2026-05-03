---
name: xlsx
description: Read, create, edit Microsoft Excel .xlsx spreadsheets with openpyxl. Use when the user mentions Excel, spreadsheets, workbooks, invoices with line items, pricing tables, client lists, budgets, or .xlsx files. Handles formulas, formatting, named ranges, charts, and cell validation.
license: MIT
source: spaice-agent-bundled
---

# XLSX

Pure-Python Excel spreadsheet manipulation via `openpyxl`.

## When to use

- User asks to read/summarise data from a .xlsx file
- User asks to create a new spreadsheet (invoice, price list, report, budget)
- User asks to edit an existing workbook (update cells, add sheets, recalculate)
- User asks to export tabular data to Excel format

## Library

```bash
pip install openpyxl
```

MIT licensed. For very large read-only workloads, prefer `pandas.read_excel()` (pandas + openpyxl under the hood).

## Common tasks

### Read — all cells from the first sheet

```python
from openpyxl import load_workbook
wb = load_workbook("data.xlsx", data_only=True)  # data_only=True evaluates formulas
ws = wb.active

for row in ws.iter_rows(values_only=True):
    print(row)
```

### Read — specific range

```python
wb = load_workbook("data.xlsx", data_only=True)
ws = wb["Pricing"]
for row in ws["A2:C50"]:
    name, qty, price = (c.value for c in row)
    print(name, qty, price)
```

### Create a new workbook — invoice

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

wb = Workbook()
ws = wb.active
ws.title = "Invoice"

# Header
ws["A1"] = "SPAICE"
ws["A1"].font = Font(size=20, bold=True)
ws["A3"] = "Invoice #"
ws["B3"] = "INV-2026-0042"

# Line items header
headers = ["Item", "Qty", "Unit price", "Line total"]
for col, h in enumerate(headers, start=1):
    cell = ws.cell(row=5, column=col, value=h)
    cell.font = Font(bold=True)
    cell.fill = PatternFill("solid", fgColor="DDDDDD")

# Line items
items = [
    ("KNX dimmer actuator 4-ch", 3, 420.00),
    ("Motion sensor outdoor",   8,  95.00),
    ("Commissioning labour",    12, 180.00),
]
for row_idx, (item, qty, price) in enumerate(items, start=6):
    ws.cell(row=row_idx, column=1, value=item)
    ws.cell(row=row_idx, column=2, value=qty)
    ws.cell(row=row_idx, column=3, value=price)
    # Line total formula — =B*C
    ws.cell(row=row_idx, column=4, value=f"=B{row_idx}*C{row_idx}")

# Total
total_row = 6 + len(items) + 1
ws.cell(row=total_row, column=3, value="TOTAL").font = Font(bold=True)
ws.cell(row=total_row, column=4, value=f"=SUM(D6:D{6+len(items)-1})").font = Font(bold=True)

# Widen columns
for col in range(1, 5):
    ws.column_dimensions[get_column_letter(col)].width = 20

wb.save("invoice.xlsx")
```

### Edit — append rows to an existing sheet

```python
from openpyxl import load_workbook
wb = load_workbook("log.xlsx")
ws = wb["Entries"]
ws.append(["2026-05-03", "Site visit Hanna residence", "2h"])
wb.save("log.xlsx")
```

### Convert sheet to CSV

```python
from openpyxl import load_workbook
import csv

wb = load_workbook("data.xlsx", data_only=True)
ws = wb.active
with open("data.csv", "w", newline="") as f:
    w = csv.writer(f)
    for row in ws.iter_rows(values_only=True):
        w.writerow(row)
```

### Read + write using pandas

```python
import pandas as pd
df = pd.read_excel("data.xlsx", sheet_name="Pricing")
df["margin"] = df["sell"] - df["cost"]
df.to_excel("data-with-margins.xlsx", index=False)
```

### Dates / times

Excel dates are stored as floats. openpyxl returns `datetime.datetime` when cell format is date. If you get a float unexpectedly, check the cell's `number_format` or set it explicitly:

```python
from datetime import datetime
ws["A1"] = datetime(2026, 5, 3)
ws["A1"].number_format = "yyyy-mm-dd"
```

## Pitfalls

- **`data_only=True` + un-saved formulas**: openpyxl returns `None` for formula cells if the workbook was never opened in Excel to recalculate. Solutions: (1) open once in Excel/LibreOffice to cache values, (2) compute in Python yourself, (3) use LibreOffice headless: `soffice --headless --calc --convert-to xlsx file.xlsx`.
- **Merged cells**: only the top-left cell has the value; other merged cells are `None`. Check `ws.merged_cells` before reading.
- **Writing formulas**: write the formula string with `=`, e.g. `=SUM(A1:A10)`. openpyxl doesn't evaluate it.
- **Large files**: for read-only on 100k+ row workbooks, use `load_workbook(..., read_only=True)` — ~10x faster.
- **Password-protected**: openpyxl can't open them. Use msoffcrypto-tool or Excel/LibreOffice first.

## Related

- `docx` — for narrative companion documents
- `pdf` — for PDF export (via LibreOffice headless or reportlab)
- `pandas` — when the workflow is analytics-heavy
