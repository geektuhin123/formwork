"""Synthetic Indian accounts-payable documents: tax invoices (GST), a PO register, goods receipt notes, supplier
emails. Everything is generated from a seed, so fixtures and eval datasets are reproducible and contain no real data."""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from typing import Any

import openpyxl
import pymupdf
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

VENDORS = [
    ("Shree Balaji Traders Pvt Ltd", "27AABCS1429B1Z6"),
    ("Kaveri Industrial Supplies", "29AAGCK4421M1ZP"),
    ("Himalaya Packaging LLP", "06AAFFH7310K1Z3"),
    ("Deccan Fasteners Pvt Ltd", "36AACCD5521L1ZV"),
    ("Sundaram Electricals", "33AAKFS9087Q1Z1"),
    ("Ganga Steel Corporation", "09AAECG3312N1Z8"),
    ("Malabar Chemicals Pvt Ltd", "32AAFCM6190R1ZT"),
    ("Narmada Polymers Ltd", "24AABCN8745P1ZC"),
]
BUYER = "Lumina Appliances India Pvt Ltd"
ITEMS = [
    ("M8 Hex Bolt Zinc Plated", "7318", 12.5),
    ("Corrugated Box 5 Ply", "4819", 48.0),
    ("Copper Winding Wire 1.2mm", "8544", 720.0),
    ("ABS Granules Natural", "3903", 142.0),
    ("LED Driver 18W", "8504", 96.5),
    ("Stretch Film Roll 23 micron", "3920", 310.0),
    ("MS Angle 40x40x5", "7216", 61.25),
    ("Epoxy Adhesive 1kg", "3506", 455.0),
    ("Ball Bearing 6204 ZZ", "8482", 88.0),
    ("PVC Insulation Tape", "3919", 14.75),
]

TEMPLATES: list[dict[str, str]] = [
    {
        "title": "TAX INVOICE",
        "vendor": "Supplier",
        "gstin": "GSTIN",
        "buyer": "Bill To",
        "number": "Invoice No",
        "date": "Invoice Date",
        "po": "PO No",
        "desc": "Description",
        "hsn": "HSN",
        "qty": "Qty",
        "rate": "Rate",
        "amount": "Amount",
        "subtotal": "Sub Total",
        "cgst": "CGST (9%)",
        "sgst": "SGST (9%)",
        "total": "Grand Total",
        "datefmt": "%d/%m/%Y",
    },
    {
        "title": "TAX INVOICE",
        "vendor": "Vendor",
        "gstin": "Vendor GSTIN",
        "buyer": "Customer",
        "number": "Invoice #",
        "date": "Date",
        "po": "Purchase Order",
        "desc": "Particulars",
        "hsn": "HSN/SAC",
        "qty": "Quantity",
        "rate": "Unit Price",
        "amount": "Line Total",
        "subtotal": "Subtotal",
        "cgst": "CGST @ 9%",
        "sgst": "SGST @ 9%",
        "total": "Total Due",
        "datefmt": "%d-%b-%Y",
    },
    {
        "title": "INVOICE",
        "vendor": "Sold By",
        "gstin": "GSTIN/UIN",
        "buyer": "Billed To",
        "number": "Bill No",
        "date": "Dated",
        "po": "PO Number",
        "desc": "Item",
        "hsn": "HSN",
        "qty": "Qty",
        "rate": "Price",
        "amount": "Value",
        "subtotal": "Taxable Value",
        "cgst": "CGST 9%",
        "sgst": "SGST 9%",
        "total": "Amount Payable",
        "datefmt": "%Y-%m-%d",
    },
]


@dataclass
class Line:
    description: str
    hsn_code: str
    quantity: int
    unit_price: float
    amount: float


@dataclass
class Invoice:
    invoice_number: str
    invoice_date: date
    vendor_name: str
    vendor_gstin: str
    buyer_name: str
    po_number: str
    currency: str
    lines: list[Line]
    subtotal: float
    cgst: float
    sgst: float
    total_amount: float
    template: int = 0

    def expected(self) -> dict[str, Any]:
        return {
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date.isoformat(),
            "vendor_name": self.vendor_name,
            "vendor_gstin": self.vendor_gstin,
            "buyer_name": self.buyer_name,
            "po_number": self.po_number,
            "currency": self.currency,
            "subtotal": self.subtotal,
            "cgst": self.cgst,
            "sgst": self.sgst,
            "total_amount": self.total_amount,
            "line_items": [
                {
                    "description": ln.description,
                    "hsn_code": ln.hsn_code,
                    "quantity": ln.quantity,
                    "unit_price": ln.unit_price,
                    "amount": ln.amount,
                }
                for ln in self.lines
            ],
        }


def money(v: float) -> str:
    return f"{v:,.2f}"


def make_invoice(i: int, seed: int = 7, n_lines: int | None = None) -> Invoice:
    rng = random.Random(seed * 1000 + i)
    vendor, gstin = VENDORS[i % len(VENDORS)]
    k = n_lines or rng.randint(2, 5)
    lines = []
    for desc, hsn, price in rng.sample(ITEMS, k):
        qty = rng.randint(2, 40) * 5
        lines.append(Line(desc, hsn, qty, price, round(qty * price, 2)))
    subtotal = round(sum(ln.amount for ln in lines), 2)
    tax = round(subtotal * 0.09, 2)
    return Invoice(
        invoice_number=f"INV-2026-{1000 + i:04d}",
        invoice_date=date(2026, 9, 1) + timedelta(days=rng.randint(0, 60)),
        vendor_name=vendor,
        vendor_gstin=gstin,
        buyer_name=BUYER,
        po_number=f"PO-2026-{100 + i:04d}",
        currency="INR",
        lines=lines,
        subtotal=subtotal,
        cgst=tax,
        sgst=tax,
        total_amount=round(subtotal + 2 * tax, 2),
        template=i % 3,
    )


def _table(c: canvas.Canvas, data: list[list[str]], x: float, y_top: float, widths: list[float]) -> float:
    t = Table(data, colWidths=widths)
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.92, 0.92, 0.92)),
            ]
        )
    )
    _w, h = t.wrapOn(c, 0, 0)
    t.drawOn(c, x, y_top - h)
    return float(y_top - h)


def invoice_pdf(inv: Invoice, extra_text: str | None = None, ruled: bool = True) -> bytes:
    """One-page digital invoice. `extra_text` is drawn in the footer (used for prompt-injection tests)."""
    t = TEMPLATES[inv.template]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(f"Invoice {inv.invoice_number}")
    _w, h = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, h - 22 * mm, t["title"])
    c.setFont("Helvetica", 10)
    y = h - 36 * mm
    for label, value in (
        (t["vendor"], inv.vendor_name),
        (t["gstin"], inv.vendor_gstin),
        (t["buyer"], inv.buyer_name),
    ):
        c.drawString(20 * mm, y, f"{label}: {value}")
        y -= 5.5 * mm
    y = h - 36 * mm
    right = [
        (t["number"], inv.invoice_number),
        (t["date"], inv.invoice_date.strftime(t["datefmt"])),
        (t["po"], inv.po_number),
        ("Currency", inv.currency),
    ]
    for label, value in right:
        c.drawString(125 * mm, y, f"{label}: {value}")
        y -= 5.5 * mm
    data = [[t["desc"], t["hsn"], t["qty"], t["rate"], t["amount"]]]
    data += [
        [ln.description, ln.hsn_code, str(ln.quantity), money(ln.unit_price), money(ln.amount)]
        for ln in inv.lines
    ]
    y = _table(c, data, 20 * mm, h - 68 * mm, [70 * mm, 22 * mm, 22 * mm, 26 * mm, 30 * mm])
    y -= 9 * mm
    c.setFont("Helvetica", 10)
    for label, amount in ((t["subtotal"], inv.subtotal), (t["cgst"], inv.cgst), (t["sgst"], inv.sgst)):
        c.drawString(125 * mm, y, f"{label}: {money(amount)}")
        y -= 5.5 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(125 * mm, y, f"{t['total']}: {money(inv.total_amount)}")
    c.setFont("Helvetica", 8)
    c.drawString(20 * mm, 24 * mm, "Goods once sold will not be taken back. Subject to Pune jurisdiction.")
    if extra_text:
        c.drawString(20 * mm, 18 * mm, extra_text)
    c.showPage()
    c.save()
    return buf.getvalue()


def scan_pdf(pdf: bytes, dpi: int = 150, skew_deg: float = 0.5, seed: int = 1, blur: float = 0.5) -> bytes:
    """Turn a digital PDF into an image-only 'scan': rasterise, skew slightly, blur, add speckle, re-wrap as PDF."""
    rng = random.Random(seed)
    src: Any = pymupdf.open(stream=pdf, filetype="pdf")
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        img = img.rotate(skew_deg, resample=Image.Resampling.BICUBIC, fillcolor=255)
        if blur:
            img = img.filter(ImageFilter.GaussianBlur(blur))
        px = img.load()
        assert px is not None
        for _ in range(int(img.width * img.height * 0.0004)):
            px[rng.randrange(img.width), rng.randrange(img.height)] = rng.choice((0, 90, 160))
        jpg = io.BytesIO()
        img.save(jpg, format="JPEG", quality=72)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=jpg.getvalue())
    data: bytes = out.tobytes(deflate=True, garbage=3)
    return data


def page_png(pdf: bytes, dpi: int = 150) -> bytes:
    doc: Any = pymupdf.open(stream=pdf, filetype="pdf")
    page = doc[0]
    png: bytes = page.get_pixmap(dpi=dpi).tobytes("png")
    return png


def two_column_pdf() -> bytes:
    """A full-width title over two text columns; paragraph gaps align across columns on purpose."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    _w, h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, h - 25 * mm, "Vendor Onboarding Policy 2026")
    c.setFont("Helvetica", 10)
    left = [
        "ALPHA one. Every new supplier must submit",
        "a GST certificate before the first order.",
        "",
        "BRAVO two. Payment terms default to forty",
        "five days from the date of goods receipt.",
        "",
        "CHARLIE three. Invoices without a valid PO",
        "number are returned within two days.",
    ]
    right = [
        "DELTA four. Goods receipt notes are raised",
        "at the gate and matched to the order.",
        "",
        "ECHO five. Short shipments are recorded",
        "and the supplier issues a credit note.",
        "",
        "FOXTROT six. Disputes are escalated to the",
        "category manager after two reminders.",
    ]
    for col_x, lines in ((20 * mm, left), (112 * mm, right)):
        y = h - 40 * mm
        for ln in lines:
            if ln:
                c.drawString(col_x, y, ln)
            y -= 5 * mm
    c.showPage()
    c.save()
    return buf.getvalue()


# ------------------------------------------------------------------ purchase orders / GRN / email
@dataclass
class Order:
    po_number: str
    po_date: date
    vendor_name: str
    item: str
    ordered_qty: int
    unit_price: float
    po_value: float
    invoice: Invoice
    received_qty: int
    grn_number: str
    grn_date: date
    seeded: list[str] = field(default_factory=list)  # conflict kinds seeded into this order


def make_order(i: int, seed: int = 11) -> Order:
    """Order i with its invoice and goods receipt. Seeds: i%5==1 invoice total differs from the PO value;
    i%5==3 short shipment (received < ordered); i%4==2 vendor name spelled differently on the invoice."""
    rng = random.Random(seed * 1000 + i)
    vendor, gstin = VENDORS[i % len(VENDORS)]
    desc, hsn, price = ITEMS[i % len(ITEMS)]
    qty = rng.randint(4, 30) * 10
    value = round(qty * price, 2)
    seeded: list[str] = []
    inv_price = price
    if i % 5 == 1:
        inv_price = round(price * 1.04, 2)
        seeded.append("amount_mismatch")
    received = qty
    if i % 5 == 3:
        received = qty - rng.randint(1, 3) * 10
        seeded.append("short_shipment")
    inv_vendor = vendor
    if i % 4 == 2:
        inv_vendor = vendor.upper().replace("PVT LTD", "PVT. LTD.")
        seeded.append("vendor_spelling")
    po_number = f"PO-2026-{200 + i:04d}"
    po_date = date(2026, 8, 1) + timedelta(days=rng.randint(0, 40))
    amount = round(qty * inv_price, 2)
    tax = round(amount * 0.09, 2)
    inv = Invoice(
        invoice_number=f"INV-{7000 + i}",
        invoice_date=po_date + timedelta(days=rng.randint(10, 25)),
        vendor_name=inv_vendor,
        vendor_gstin=gstin,
        buyer_name=BUYER,
        po_number=po_number,
        currency="INR",
        lines=[Line(desc, hsn, qty, inv_price, amount)],
        subtotal=amount,
        cgst=tax,
        sgst=tax,
        total_amount=round(amount + 2 * tax, 2),
        template=i % 3,
    )
    return Order(
        po_number,
        po_date,
        vendor,
        desc,
        qty,
        price,
        value,
        inv,
        received,
        f"GRN-{5000 + i}",
        inv.invoice_date - timedelta(days=rng.randint(1, 6)),
        seeded,
    )


def po_register_xlsx(orders: list[Order]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "PO Register"
    ws.append(["PO Register FY 2026-27"])
    ws.append([])
    ws.append(["PO No", "PO Date", "Supplier", "Item", "Ordered Qty", "Rate", "PO Value (INR)"])
    for o in orders:
        ws.append(
            [
                o.po_number,
                o.po_date.strftime("%d-%m-%Y"),
                o.vendor_name,
                o.item,
                o.ordered_qty,
                o.unit_price,
                f"Rs. {money(o.po_value)}",
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def grn_pdf(o: Order) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    _w, h = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, h - 22 * mm, "GOODS RECEIPT NOTE")
    c.setFont("Helvetica", 11)
    y = h - 38 * mm
    for label, value in (
        ("GRN No", o.grn_number),
        ("GRN Date", o.grn_date.strftime("%d/%m/%Y")),
        ("PO No", o.po_number),
        ("Supplier", o.vendor_name),
    ):
        c.drawString(20 * mm, y, f"{label}: {value}")
        y -= 7 * mm
    y = _table(
        c,
        [["Item", "Ordered", "Received"], [o.item, str(o.ordered_qty), str(o.received_qty)]],
        20 * mm,
        y - 4 * mm,
        [90 * mm, 30 * mm, 30 * mm],
    )
    c.setFont("Helvetica", 11)
    c.drawString(20 * mm, y - 10 * mm, f"Total Received Qty: {o.received_qty}")
    c.drawString(20 * mm, y - 17 * mm, "Received in good condition. Stores In-charge: R. Kulkarni")
    c.showPage()
    c.save()
    return buf.getvalue()


def supplier_eml(o: Order, attachment: bytes | None = None, attachment_name: str = "invoice.pdf") -> bytes:
    msg = EmailMessage()
    msg["From"] = "accounts@" + o.vendor_name.split()[0].lower() + ".example"
    msg["To"] = "ap@lumina.example"
    msg["Subject"] = f"Invoice {o.invoice.invoice_number} against {o.po_number}"
    msg["Date"] = o.invoice.invoice_date.strftime("%a, %d %b %Y 10:15:00 +0530")
    msg["Message-ID"] = f"<{o.invoice.invoice_number}@supplier.example>"
    msg.set_content(
        f"Dear Accounts Team,\n\nPlease find attached our invoice against your order.\n\n"
        f"PO No: {o.po_number}\nInvoice No: {o.invoice.invoice_number}\n"
        f"Grand Total: {money(o.invoice.total_amount)}\n\n"
        f"Kindly release payment within the agreed terms.\n\nRegards,\n{o.vendor_name}\n"
    )
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=attachment_name)
    return msg.as_bytes()


def two_table_xlsx() -> bytes:
    """One sheet, two table islands (side by side would also work; these are stacked with a gap) and a merged cell."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Sheet1"
    ws.append(["Region", "Vendor", "Spend"])
    ws.append(["West", "Shree Balaji Traders Pvt Ltd", 120000])
    ws.append([None, "Narmada Polymers Ltd", 84000])
    ws.append(["South", "Kaveri Industrial Supplies", 56000])
    ws.merge_cells("A2:A3")
    for r, row in enumerate(
        [["PO No", "Status"], ["PO-2026-0201", "Open"], ["PO-2026-0202", "Closed"]], start=7
    ):
        for c_idx, v in enumerate(row, start=5):
            ws.cell(row=r, column=c_idx, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def messy_invoice_xlsx(invoices: list[Invoice], rows: int | None = None) -> bytes:
    """Invoice register with off-schema headers, currency strings and mixed date formats. `rows` repeats the data."""
    wb = openpyxl.Workbook(write_only=rows is not None and rows > 5000)
    ws = wb.create_sheet("Register") if wb.write_only else wb.active
    assert ws is not None
    ws.title = "Register"
    ws.append(
        [
            "Bill No",
            "Bill Dt",
            "Supplier Name",
            "Supplier GSTIN",
            "PO Ref",
            "Taxable Amt",
            "Total Amt (INR)",
            "Remarks",
        ]
    )
    n = rows or len(invoices)
    for i in range(n):
        inv = invoices[i % len(invoices)]
        number = inv.invoice_number if i < len(invoices) else f"{inv.invoice_number}-{i}"
        fmt = "%d/%m/%Y" if i % 2 else "%d-%b-%Y"
        ws.append(
            [
                number,
                inv.invoice_date.strftime(fmt),
                f"  {inv.vendor_name} ",
                inv.vendor_gstin,
                inv.po_number,
                f"Rs. {money(inv.subtotal)}",
                f"₹{money(inv.total_amount)}",
                "ok" if i % 3 else "",
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def policy_docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_heading("Purchase Order Amendment", level=1)
    d.add_paragraph("PO No: PO-2026-0207")
    d.add_paragraph("The delivery date is extended by ten days at the supplier's request.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Field", "New value"
    t.cell(1, 0).text, t.cell(1, 1).text = "Delivery Date", "2026-10-15"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
