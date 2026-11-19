import pytest
from formwork_core.context import JobContext
from formwork_testkit import synth


@pytest.fixture(scope="session")
def fixtures() -> dict[str, bytes]:
    """The Phase 1 fixture set, generated deterministically (small, synthetic, no real data)."""
    o = synth.make_order(3)
    return {
        "digital_1.pdf": synth.invoice_pdf(synth.make_invoice(0)),
        "digital_2.pdf": synth.invoice_pdf(synth.make_invoice(1)),
        "scanned_1.pdf": synth.scan_pdf(synth.invoice_pdf(synth.make_invoice(2))),
        "scanned_2.pdf": synth.scan_pdf(synth.grn_pdf(o), seed=2),
        "two_column.pdf": synth.two_column_pdf(),
        "two_tables.xlsx": synth.two_table_xlsx(),
        "supplier.eml": synth.supplier_eml(o, synth.invoice_pdf(o.invoice)),
    }


@pytest.fixture
def ctx() -> JobContext:
    return JobContext.for_tests()
