---
name: pdf
description: Read, write, merge, split, extract text from, and fill fillable forms in PDF files. Use when the user mentions PDFs, invoices, forms, receipts, contracts, or anything ending in .pdf. Wraps pypdf + pdfplumber for extraction, reportlab for creation, pikepdf for form filling. All libraries are permissively licensed and commonly pre-installed or `pip install`-able.
license: MIT
source: spaice-agent-bundled
---

# PDF

Pure-Python PDF manipulation. No subscriptions, no cloud, no tracking.

## When to use

- User asks to read/summarise/extract from a PDF
- User asks to create a PDF (invoice, report, contract)
- User asks to merge multiple PDFs into one
- User asks to split a PDF or extract specific pages
- User asks to fill a form field in a PDF
- User asks to convert a PDF to images (for vision/OCR)

## Libraries

| Library | Use for | Install |
|---|---|---|
| `pypdf` | Metadata, merge, split, encrypt, decrypt | `pip install pypdf` |
| `pdfplumber` | Text + table extraction (layout-aware) | `pip install pdfplumber` |
| `reportlab` | Generate NEW PDFs (invoices, reports) | `pip install reportlab` |
| `pikepdf` | Form filling, page manipulation | `pip install pikepdf` |
| `pdf2image` | PDF pages → PNG (for vision) | `pip install pdf2image` (needs poppler) |

## Common tasks

### Extract all text

```python
import pdfplumber
with pdfplumber.open("file.pdf") as pdf:
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
print(text)
```

### Extract tables

```python
import pdfplumber
with pdfplumber.open("file.pdf") as pdf:
    tables = []
    for page in pdf.pages:
        tables.extend(page.extract_tables() or [])
# tables is list[list[list[str]]]
```

### Merge multiple PDFs

```python
from pypdf import PdfWriter
writer = PdfWriter()
for path in ["a.pdf", "b.pdf", "c.pdf"]:
    writer.append(path)
with open("merged.pdf", "wb") as f:
    writer.write(f)
```

### Split — extract pages 3-5

```python
from pypdf import PdfReader, PdfWriter
reader = PdfReader("source.pdf")
writer = PdfWriter()
for page in reader.pages[2:5]:  # 0-indexed, exclusive end
    writer.add_page(page)
with open("pages-3-5.pdf", "wb") as f:
    writer.write(f)
```

### Create a new PDF (invoice-style)

```python
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

c = canvas.Canvas("invoice.pdf", pagesize=A4)
w, h = A4
c.setFont("Helvetica-Bold", 24)
c.drawString(50, h - 80, "INVOICE")
c.setFont("Helvetica", 12)
c.drawString(50, h - 120, "Client: Acme Corp")
c.drawString(50, h - 140, "Date: 2026-05-03")
c.drawString(50, h - 160, "Amount due: $1,234.00")
c.showPage()
c.save()
```

For styled invoices, prefer `pdy` skill if available — SPAICE-branded WeasyPrint pipeline that handles markdown → PDF with stylesheets.

### Fill a fillable form

```python
from pikepdf import Pdf
with Pdf.open("form.pdf") as pdf:
    for field in pdf.Root.AcroForm.Fields:
        name = str(field.T)
        if name == "full_name":
            field.V = "Jozef Doboš"
        elif name == "amount":
            field.V = "$1,234.00"
    pdf.save("filled.pdf")
```

### PDF pages → images (for LLM vision)

```python
from pdf2image import convert_from_path
images = convert_from_path("doc.pdf", dpi=200)
for i, img in enumerate(images):
    img.save(f"page-{i+1}.png", "PNG")
```

## Pitfalls

- **pdfplumber on scanned PDFs**: returns empty/garbage. Use `pdf2image` + vision model instead.
- **reportlab coordinates**: origin is bottom-left (not top-left). `h - y` for top-oriented layouts.
- **pikepdf + encrypted PDFs**: supply `password=` kwarg to `Pdf.open(...)`.
- **poppler for pdf2image on macOS**: `brew install poppler`. On Ubuntu: `apt install poppler-utils`.
- **Large PDFs + memory**: iterate pages, don't load all at once.

## Related

- `pdy` — SPAICE-branded markdown → PDF (use for branded deliverables)
- `xlsx` — when the source is a spreadsheet being exported to PDF
- `ocr-and-documents` — when the PDF is scanned and needs OCR before text extraction
