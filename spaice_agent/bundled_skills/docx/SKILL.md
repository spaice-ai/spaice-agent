---
name: docx
description: Read, create, edit Microsoft Word .docx files with python-docx. Use when the user mentions Word documents, reports, letters, contracts, specs, or .docx files. Handles headings, paragraphs, tables, images, styles, tracked changes, headers/footers, and comments.
license: MIT
source: spaice-agent-bundled
---

# DOCX

Pure-Python Word document manipulation via `python-docx`.

## When to use

- User asks to read/summarise content from a .docx file
- User asks to create a new Word document (letter, contract, spec, report)
- User asks to edit an existing .docx (replace text, update table, change style)
- User asks to generate a Word deliverable from structured data

## Library

```bash
pip install python-docx
```

MIT licensed. No subscriptions, no cloud.

## Common tasks

### Read — extract all text + tables

```python
from docx import Document
doc = Document("input.docx")

# Paragraphs
for para in doc.paragraphs:
    print(para.text)

# Tables
for table in doc.tables:
    for row in table.rows:
        print(" | ".join(cell.text for cell in row.cells))
```

### Create a new document

```python
from docx import Document
from docx.shared import Inches, Pt

doc = Document()
doc.add_heading("Project Proposal", level=0)
doc.add_paragraph("Prepared for Acme Corp")
doc.add_paragraph("2026-05-03").italic = True

doc.add_heading("Scope", level=1)
doc.add_paragraph(
    "Design, supply, and install a complete residential control system "
    "covering lighting, shading, climate, AV, security, and intercom."
)

doc.add_heading("Line items", level=1)
table = doc.add_table(rows=1, cols=3)
table.style = "Light Grid Accent 1"
hdr = table.rows[0].cells
hdr[0].text = "Item"
hdr[1].text = "Qty"
hdr[2].text = "Unit price"
for item, qty, price in [("KNX dimmer", 12, "$180"), ("Motion sensor", 8, "$95")]:
    row = table.add_row().cells
    row[0].text = item
    row[1].text = str(qty)
    row[2].text = price

doc.add_page_break()
doc.add_paragraph("Terms: Net 30. Contact jozef@spaice.ai for clarifications.")

doc.save("proposal.docx")
```

### Edit an existing document — replace placeholder text

```python
from docx import Document
doc = Document("template.docx")

replacements = {
    "{{CLIENT}}": "Acme Corp",
    "{{DATE}}": "2026-05-03",
    "{{AMOUNT}}": "$12,345",
}

for para in doc.paragraphs:
    for key, val in replacements.items():
        if key in para.text:
            # Preserve run formatting by editing runs, not full text
            for run in para.runs:
                if key in run.text:
                    run.text = run.text.replace(key, val)

doc.save("filled.docx")
```

### Add an image

```python
from docx import Document
from docx.shared import Inches

doc = Document()
doc.add_heading("Site photos", level=1)
doc.add_picture("photo.png", width=Inches(5.0))
doc.save("with-image.docx")
```

### Apply a style to a paragraph

```python
from docx import Document
from docx.shared import Pt, RGBColor

doc = Document()
p = doc.add_paragraph()
run = p.add_run("Important note")
run.bold = True
run.font.size = Pt(14)
run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)  # dark red
doc.save("styled.docx")
```

### Read/write document comments

python-docx doesn't expose comments directly — use `python-docx-ng` (fork) or manipulate the underlying XML via `doc.part.element`. For most tasks (create + basic edit), stock python-docx is enough.

## Pitfalls

- **Styles**: names are locale-specific. English template uses "Heading 1", Spanish uses "Título 1". Safest: create your own style once with `doc.styles.add_style(...)` and reuse.
- **Tables inside tables**: supported but verbose. Avoid unless required.
- **Run vs paragraph**: formatting is on runs, not paragraphs. Always edit runs when preserving bold/italic/colour.
- **Track changes**: python-docx can't accept/reject changes. Use LibreOffice CLI (`soffice --headless --convert-to docx`) as a fallback for that workflow.
- **Large documents (>100 pages)**: parsing is fast, but iterating all paragraphs/tables can be slow. Use `doc.element` + XPath for targeted queries.

## Related

- `xlsx` — when the source data is tabular (generate docx from spreadsheet)
- `pdf` — to convert docx → PDF for distribution (use LibreOffice headless)
- `powerpoint` — when the output should be a deck, not a doc
