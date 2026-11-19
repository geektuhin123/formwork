"""Target schemas used by tests and eval datasets (as JSON Schema, compiled by the schema compiler)."""

from __future__ import annotations

from typing import Any

GSTIN_PATTERN = "^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$"


def invoice_json_schema() -> dict[str, Any]:
    return {
        "title": "AP invoices (GST)",
        "type": "object",
        "x-entity-key": ["invoice_number"],
        "x-record-grain": "one row per invoice",
        "required": [
            "invoice_number",
            "invoice_date",
            "vendor_name",
            "subtotal",
            "total_amount",
            "line_items",
        ],
        "properties": {
            "invoice_number": {
                "type": "string",
                "description": "The supplier's invoice number, labelled 'Invoice No', 'Invoice #' or 'Bill No'",
            },
            "invoice_date": {
                "type": "string",
                "format": "date",
                "description": "Date the invoice was issued, labelled 'Invoice Date', 'Date' or 'Dated'",
            },
            "vendor_name": {
                "type": "string",
                "description": "Legal name of the supplier who issued the invoice, labelled 'Supplier', 'Vendor' or 'Sold By'",
            },
            "vendor_gstin": {
                "type": "string",
                "pattern": GSTIN_PATTERN,
                "description": "Supplier's 15-character GST number, labelled 'GSTIN', 'Vendor GSTIN' or 'GSTIN/UIN'",
            },
            "buyer_name": {
                "type": "string",
                "description": "Name of the company being billed, labelled 'Bill To', 'Customer' or 'Billed To'",
            },
            "po_number": {
                "type": "string",
                "description": "Purchase order the invoice refers to, labelled 'PO No', 'Purchase Order' or 'PO Number'",
            },
            "currency": {
                "type": "string",
                "x-type": "currency",
                "description": "ISO 4217 currency of the invoice amounts, labelled 'Currency'",
            },
            "subtotal": {
                "type": "number",
                "description": "Amount before tax, labelled 'Sub Total', 'Subtotal' or 'Taxable Value'",
                "x-rules": ["approx(subtotal, total(line_items, 'amount'), 0.05)"],
            },
            "cgst": {
                "type": "number",
                "description": "Central GST amount in currency (not the rate), labelled 'CGST'",
            },
            "sgst": {
                "type": "number",
                "description": "State GST amount in currency (not the rate), labelled 'SGST'",
            },
            "total_amount": {
                "type": "number",
                "description": "Final amount payable including all taxes, labelled 'Grand Total', 'Total Due' or 'Amount Payable'",
                "x-rules": ["approx(total_amount, subtotal + cgst + sgst, 0.05)"],
            },
            "line_items": {
                "type": "array",
                "description": "One row per invoiced item",
                "items": {
                    "type": "object",
                    "required": ["description", "amount"],
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Item description, column 'Description', 'Particulars' or 'Item'",
                        },
                        "hsn_code": {
                            "type": "string",
                            "description": "HSN or SAC classification code, column 'HSN' or 'HSN/SAC'",
                        },
                        "quantity": {
                            "type": "number",
                            "description": "Quantity invoiced for the row, column 'Qty' or 'Quantity'",
                        },
                        "unit_price": {
                            "type": "number",
                            "description": "Price per unit for the row, column 'Rate', 'Unit Price' or 'Price'",
                        },
                        "amount": {
                            "type": "number",
                            "description": "Row amount before tax, column 'Amount', 'Line Total' or 'Value'",
                            "x-rules": ["approx(amount, quantity * unit_price, 0.05)"],
                        },
                    },
                },
            },
        },
    }


def reconciliation_json_schema() -> dict[str, Any]:
    """PO vs invoice vs goods receipt: one record per purchase order, merged from three sources."""
    return {
        "title": "PO reconciliation",
        "type": "object",
        "x-entity-key": ["po_number"],
        "x-record-grain": "one row per purchase order",
        "required": [
            "po_number",
            "vendor_name",
            "invoice_number",
            "ordered_qty",
            "received_qty",
            "po_value",
            "invoice_subtotal",
        ],
        "properties": {
            "po_number": {
                "type": "string",
                "description": "Purchase order number shared by all documents, labelled 'PO No', 'Purchase Order' or 'PO Number'",
            },
            "vendor_name": {
                "type": "string",
                "x-merge-policy": "require_agreement",
                "description": "Legal name of the supplier, labelled 'Supplier', 'Vendor' or 'Sold By'",
            },
            "po_date": {
                "type": "string",
                "format": "date",
                "description": "Date the purchase order was raised, labelled 'PO Date'",
            },
            "invoice_number": {
                "type": "string",
                "description": "The supplier's invoice number, labelled 'Invoice No', 'Invoice #' or 'Bill No'",
            },
            "invoice_date": {
                "type": "string",
                "format": "date",
                "description": "Date the invoice was issued, labelled 'Invoice Date', 'Date' or 'Dated'",
            },
            "grn_number": {
                "type": "string",
                "description": "Goods receipt note number from the stores, labelled 'GRN No'",
            },
            "ordered_qty": {
                "type": "number",
                "x-merge-policy": "require_agreement",
                "description": "Quantity ordered on the purchase order, labelled 'Ordered Qty' or 'Ordered'",
            },
            "received_qty": {
                "type": "number",
                "description": "Total quantity received at the gate, labelled 'Total Received Qty'",
                "x-rules": ["received_qty == ordered_qty"],
            },
            "po_value": {
                "type": "number",
                "description": "Order value before tax from the PO register, labelled 'PO Value'",
            },
            "invoice_subtotal": {
                "type": "number",
                "description": "Invoice amount before tax, labelled 'Sub Total', 'Subtotal' or 'Taxable Value'",
                "x-rules": ["approx(invoice_subtotal, po_value, 1.0)"],
            },
            "invoice_total": {
                "type": "number",
                "description": "Invoice amount payable including taxes, labelled 'Grand Total', 'Total Due' or 'Amount Payable'",
            },
        },
    }
