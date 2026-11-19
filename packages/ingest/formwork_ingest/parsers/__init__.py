from formwork_ingest.parsers.csv import CsvParser
from formwork_ingest.parsers.docx import DocxParser
from formwork_ingest.parsers.email import EmailParser
from formwork_ingest.parsers.image import ImageParser
from formwork_ingest.parsers.pdf import PdfParser
from formwork_ingest.parsers.sheet import SpreadsheetParser

PARSERS = [PdfParser, ImageParser, SpreadsheetParser, CsvParser, DocxParser, EmailParser]

__all__ = [
    "PARSERS",
    "CsvParser",
    "DocxParser",
    "EmailParser",
    "ImageParser",
    "PdfParser",
    "SpreadsheetParser",
]
