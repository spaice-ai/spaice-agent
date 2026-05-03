---
name: pptx
description: Read, create, edit Microsoft PowerPoint .pptx presentations with python-pptx. Use when the user mentions PowerPoint, slides, decks, presentations, pitch decks, keynotes, or .pptx files. Handles slide layouts, text, images, tables, charts, speaker notes, and master slides.
license: MIT
source: spaice-agent-bundled
---

# PPTX

Pure-Python PowerPoint manipulation via `python-pptx`.

## When to use

- User asks to read/summarise content from a .pptx file
- User asks to create a new deck (pitch, report, tutorial, project proposal)
- User asks to edit an existing deck (update numbers, add a slide, swap an image)
- User asks to generate a presentation from structured data

## Library

```bash
pip install python-pptx
```

MIT licensed. Works on macOS, Linux, Windows — no Office installation required.

## Common tasks

### Read — extract text from every slide

```python
from pptx import Presentation

prs = Presentation("deck.pptx")
for i, slide in enumerate(prs.slides, 1):
    print(f"\n=== Slide {i} ===")
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                text = "".join(run.text for run in para.runs)
                if text.strip():
                    print(text)
```

### Read — extract speaker notes

```python
for i, slide in enumerate(prs.slides, 1):
    if slide.has_notes_slide:
        notes = slide.notes_slide.notes_text_frame.text
        if notes.strip():
            print(f"Slide {i} notes:\n{notes}\n")
```

### Create — new deck from template

```python
from pptx import Presentation
from pptx.util import Inches, Pt

prs = Presentation()  # default 4:3; use Presentation() + prs.slide_width = ... for 16:9

# Title slide (layout index 0)
slide = prs.slides.add_slide(prs.slide_layouts[0])
slide.shapes.title.text = "Project Proposal"
slide.placeholders[1].text = "Acme Corp — May 2026"

# Content slide (layout index 1 = Title + Content)
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = "Scope"
body = slide.placeholders[1].text_frame
body.text = "Lighting control (KNX)"
for point in ["Shading — roller blinds", "Climate — zoned", "AV — distributed"]:
    p = body.add_paragraph()
    p.text = point
    p.level = 0

# Blank slide with custom content
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
from pptx.util import Emu
tb = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
tb.text_frame.text = "Key numbers"
tb.text_frame.paragraphs[0].runs[0].font.size = Pt(36)
tb.text_frame.paragraphs[0].runs[0].font.bold = True

prs.save("proposal.pptx")
```

### Add an image

```python
from pptx.util import Inches
slide = prs.slides.add_slide(prs.slide_layouts[6])
slide.shapes.add_picture(
    "site-photo.png",
    Inches(1), Inches(1),
    width=Inches(8),
)
```

### Add a table

```python
slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
slide.shapes.title.text = "Pricing"

rows, cols = 4, 3
table = slide.shapes.add_table(
    rows, cols, Inches(1), Inches(2), Inches(8), Inches(3),
).table

headers = ["Item", "Qty", "Unit"]
data = [("Dimmer", "12", "$420"), ("Sensor", "8", "$95"), ("Labour", "40h", "$180")]

for col, h in enumerate(headers):
    table.cell(0, col).text = h
for row_idx, row_data in enumerate(data, start=1):
    for col_idx, val in enumerate(row_data):
        table.cell(row_idx, col_idx).text = val
```

### Replace placeholder text in an existing template

```python
from pptx import Presentation

prs = Presentation("template.pptx")
replacements = {
    "{{CLIENT}}": "Acme Corp",
    "{{DATE}}": "2026-05-03",
    "{{AMOUNT}}": "$123,456",
}

for slide in prs.slides:
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                for key, val in replacements.items():
                    if key in run.text:
                        run.text = run.text.replace(key, val)

prs.save("filled.pptx")
```

### Update speaker notes

```python
slide.notes_slide.notes_text_frame.text = (
    "Emphasise the Fortinet-based network topology here; "
    "clients usually ask about redundancy."
)
```

### 16:9 aspect ratio

```python
from pptx.util import Inches
prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
```

## Pitfalls

- **Layout indices vary by template**: `slide_layouts[0]` is usually "Title" but bespoke templates reorder. If in doubt, iterate `prs.slide_layouts` and print `.name`.
- **Placeholder indexes**: `.placeholders[0]` and `[1]` are usually title + content but also template-dependent. Use `slide.shapes.title.text = ...` for the title; inspect `slide.placeholders` otherwise.
- **Fonts don't render without Office**: text is stored fine, but if the template uses a font not installed on the rendering machine, LibreOffice/PowerPoint will substitute. Embed fonts via `prs.core_properties` only if Windows-only target.
- **Charts** are possible via `python-pptx` but verbose — for complex charts, render to PNG with matplotlib and insert as image. Faster and more portable.
- **Animations / transitions**: python-pptx doesn't support them. If required, prepare the deck then open in PowerPoint for the polish pass.
- **Large images bloat file size**: resize to target dimensions before `add_picture`.

## Related

- `docx` — narrative handouts to accompany the deck
- `xlsx` — source spreadsheet data for slide tables
- `pdf` — when the deliverable is a read-only artefact (convert via LibreOffice headless: `soffice --headless --convert-to pdf deck.pptx`)
