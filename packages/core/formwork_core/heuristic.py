"""HeuristicModel: a rule-based stand-in for an LLM, used by FakeProvider when no API key is configured.

It reads exactly what a real model would read (the prompt and the tool schema) and answers the same tool calls
by label matching. Field descriptions drive it: quoted phrases in a description ("labelled 'PO No'") become
labels, so a weak description extracts worse - the same failure mode a real model has, in miniature.

It exists so unit tests, the dev stack and *offline* evals exercise the full pipeline deterministically.
Numbers produced with it say the plumbing works; they say nothing about real model quality.

Options:
- error_rate / seed: deterministic fault injection on the cheap tier (omit, wrong-neighbour, hallucinated quote),
  so the repair loop has something to repair in offline evals.
- gullible: obey "ignore previous instructions and set <field> to <value>" text found in documents. Used by the
  prompt-injection tests to prove the pipeline's own defences hold even against a model that falls for it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

from formwork_core.prompting import DOC_CLASSES, parse_blocks
from formwork_core.provider import LLMRequest, ModelTier

_CLASS_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("goods_receipt", ("goods receipt", "goods received", "grn")),
    ("purchase_order", ("purchase order", "po register", "ordered qty", "order date")),
    ("credit_note", ("credit note",)),
    ("delivery_challan", ("delivery challan",)),
    ("bank_statement", ("statement of account", "bank statement", "closing balance")),
    ("invoice", ("tax invoice", "invoice")),
    ("email", ("subject:", "from:")),
]

_ABBREV = {
    "number": ["no", "no.", "#", "num", "nr"],
    "quantity": ["qty", "qty."],
    "amount": ["amt", "value"],
    "description": ["particulars", "item", "item description", "desc"],
    "unit price": ["rate", "price", "unit rate"],
    "total amount": ["grand total", "total due", "amount payable", "invoice total", "net payable", "total"],
    "vendor": ["supplier", "seller", "sold by"],
    "buyer": ["bill to", "customer", "billed to"],
    "date": ["dated", "dt"],
    "purchase order": ["po"],
    "hsn code": ["hsn", "hsn/sac"],
    "received": ["recd", "rcvd"],
}
_NUM = re.compile(r"\(?[-+]?\d[\d,]*(?:\.\d+)?\)?")
_DATE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}[ \-](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ \-,]+\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2},? \d{4})\b",
    re.I,
)
_INJECTION = re.compile(
    r"ignore (?:all )?(?:the )?previous instructions and set (?:the )?([a-z_ ]+?) to ([\w.,\-]+)", re.I
)
_CURRENCIES = {
    "inr": "INR",
    "rs": "INR",
    "rs.": "INR",
    "₹": "INR",
    "usd": "USD",
    "$": "USD",
    "eur": "EUR",
    "€": "EUR",
    "gbp": "GBP",
    "£": "GBP",
}


def _tag(prompt: str, name: str) -> str | None:
    m = re.search(rf"<{name}>\n?(.*?)\n?</{name}>", prompt, re.S)
    return m.group(1) if m else None


def _words(name: str) -> str:
    return re.sub(r"[_\W]+", " ", name).strip().lower()


def labels_for(field: dict[str, Any]) -> list[str]:
    """Candidate labels for a field, most specific first."""
    name = _words(str(field.get("name") or field.get("path", "")).split(".")[-1].replace("[]", ""))
    out = [name]
    desc = str(field.get("description", ""))
    quoted = re.findall(r"(?<![A-Za-z0-9])['\"“‘]([^'\"”’]{2,40})['\"”’](?![A-Za-z0-9])", desc)
    out += [q.lower().strip() for q in quoted]
    for full, shorts in _ABBREV.items():
        if full in name:
            out += [name.replace(full, s) for s in shorts]
        if full == name:
            out += shorts
    seen: list[str] = []
    for lab in out:
        lab = re.sub(r"\s+", " ", lab).strip()
        if lab and lab not in seen:
            seen.append(lab)
    return sorted(seen, key=len, reverse=True)


def _parse_number(tok: str) -> float | None:
    neg = tok.startswith("(") and tok.endswith(")")
    try:
        v = float(tok.strip("()").replace(",", ""))
    except ValueError:
        return None
    return -v if neg else v


def parse_value(ftype: str, rest: str, field: dict[str, Any]) -> Any:
    rest = rest.strip()
    if not rest:
        return None
    if ftype in ("number", "integer"):
        for m in _NUM.finditer(rest):
            if rest[m.end() : m.end() + 1] == "%" or (m.start() > 0 and rest[m.start() - 1].isalpha()):
                continue
            v = _parse_number(m.group())
            if v is not None:
                return int(v) if ftype == "integer" and v == int(v) else v
        return None
    if ftype in ("date", "datetime"):
        dm = _DATE.search(rest)
        return dm.group(1) if dm else None
    if ftype == "currency":
        for tok in re.findall(r"[A-Za-z₹$€£.]+", rest):
            if tok.lower() in _CURRENCIES:
                return _CURRENCIES[tok.lower()]
        return None
    if ftype == "boolean":
        low = rest.lower()
        if re.match(r"(yes|true|y|✓|x\b)", low):
            return True
        if re.match(r"(no|false|n\b)", low):
            return False
        return None
    if ftype == "enum":
        for ev in field.get("enum_values") or []:
            if ev.lower() in rest.lower():
                return ev
        return None
    val = re.split(r"\s{2,}|\s\|\s|\|", rest)[0].strip(" :-|")
    return val or None


def _label_score(header: str, label: str) -> float:
    if header == label:
        return 1.0
    if re.search(rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])", header) and len(label) >= 4:
        return 0.9
    return difflib.SequenceMatcher(None, header, label).ratio() * 0.9


def _split_row(ln: str) -> list[str]:
    return [c.strip() for c in ln.strip().strip("|").split("|")]


def _field_key(field: dict[str, Any]) -> str:
    return str(field["key"]) if "key" in field else str(field["path"]).replace("[]", "").replace(".", "__")


class HeuristicModel:
    def __init__(self, error_rate: float = 0.0, seed: int = 0, gullible: bool = False) -> None:
        self.error_rate = error_rate
        self.seed = seed
        self.gullible = gullible

    # ------------------------------------------------------------ dispatch
    def __call__(self, request: LLMRequest) -> dict[str, Any]:
        purpose = request.purpose.split(":")[0]
        if purpose == "classify":
            return self._classify(request)
        if purpose == "schema":
            return self._describe(request) if request.purpose.endswith("describe") else self._draft(request)
        if purpose == "mapping_plan":
            return self._mapping_plan(request)
        if purpose == "extract":
            return self._extract(request)
        raise ValueError(f"HeuristicModel has no handler for purpose {request.purpose!r}")

    # ------------------------------------------------------------ classify
    def _classify(self, request: LLMRequest) -> dict[str, Any]:
        text = " ".join(b[2] for b in parse_blocks(request.prompt)).lower()[:6000]
        allowed = request.tool.input_schema["properties"]["doc_class"].get("enum", DOC_CLASSES)
        best, score = "other", 0
        for cls, hints in _CLASS_HINTS:
            s = sum(text.count(h) + (6 if 0 <= text.find(h) < 120 else 0) for h in hints)
            if s > score and cls in allowed:
                best, score = cls, s
        return {"doc_class": best, "confidence": 0.9 if score else 0.4}

    # ------------------------------------------------------------ schema compiler
    def _describe(self, request: LLMRequest) -> dict[str, Any]:
        cols = json.loads(_tag(request.prompt, "columns") or "[]")
        fields = []
        for c in cols:
            words = _words(c["name"])
            sample = next((s for s in c.get("samples", []) if s), "")
            fields.append(
                {
                    "name": c["name"],
                    "description": f"The {words} of the record as stated in the source document, labelled '{words}'"
                    + (f", for example {sample}" if sample else ""),
                }
            )
        keys = [c["name"] for c in cols if re.search(r"(number|_no|_id|\bid)$", c["name"].lower())][:1]
        if not keys and cols:
            keys = [cols[0]["name"]]
        subject = (
            _words(keys[0]).replace(" number", "").replace(" no", "").replace(" id", "") if keys else "record"
        )
        return {"fields": fields, "entity_key": keys, "record_grain": f"one row per {subject}"}

    def _draft(self, request: LLMRequest) -> dict[str, Any]:
        req = _tag(request.prompt, "request") or request.prompt
        listing = req.split(":", 1)[1] if ":" in req else req
        names = [
            n
            for n in (
                re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_") for p in re.split(r",|;|\band\b|\n", listing)
            )
            if n
        ]
        names = [n[:40] for n in names if len(n) > 1][:40] or ["value"]
        fields = []
        for n in names:
            ftype = "string"
            if re.search(r"(date|_on$)", n):
                ftype = "date"
            elif re.search(r"(amount|total|price|qty|quantity|tax|gst|rate|balance)", n):
                ftype = "number"
            fields.append(
                {
                    "path": n,
                    "type": ftype,
                    "required": True,
                    "description": f"The {_words(n)} stated in the document, labelled '{_words(n)}'",
                }
            )
        key = next((n for n in names if re.search(r"(number|_no|_id)$", n)), names[0])
        return {
            "name": "Drafted schema",
            "record_grain": f"one row per {_words(key)}",
            "entity_key": [key],
            "fields": fields,
        }

    # ------------------------------------------------------------ tabular mapping plan
    def _mapping_plan(self, request: LLMRequest) -> dict[str, Any]:
        header: list[str] = json.loads(_tag(request.prompt, "header") or "[]")
        fields: list[dict[str, Any]] = json.loads(_tag(request.prompt, "fields") or "[]")
        cols = []
        used: set[str] = set()
        norm = {h: _words(h) for h in header}
        for f in fields:
            best, best_score = None, 0.0
            for lab_rank, lab in enumerate(labels_for({**f, "name": str(f["path"]).split(".")[-1]})):
                for h, hn in norm.items():
                    if h in used or not hn:
                        continue
                    score = _label_score(hn, lab) - 0.01 * lab_rank
                    if score > best_score:
                        best, best_score = h, score
            if best is not None and best_score >= 0.74:
                used.add(best)
                transform = {
                    "number": "to_number",
                    "integer": "to_number",
                    "date": "parse_date",
                    "datetime": "parse_date",
                }.get(str(f["type"]), "identity")
                cols.append({"source_column": best, "field_path": f["path"], "transform": transform})
        return {"columns": cols}

    # ------------------------------------------------------------ extraction
    def _faulty(self, request: LLMRequest, block_id: str, key: str) -> int | None:
        if self.error_rate <= 0 or request.tier is not ModelTier.CHEAP:
            return None
        h = int(hashlib.sha256(f"{self.seed}|{block_id}|{key}".encode()).hexdigest()[:8], 16)
        return h % 3 if (h / 0xFFFFFFFF) < self.error_rate else None

    def _extract(self, request: LLMRequest) -> dict[str, Any]:
        fields: list[dict[str, Any]] = json.loads(_tag(request.system + request.prompt, "fields") or "[]")
        blocks = parse_blocks(request.prompt)
        if "rows" in request.tool.input_schema.get("properties", {}):
            return {"rows": self._extract_rows(request, fields, blocks)}
        out = self._extract_scalars(request, fields, blocks)
        if self.gullible:
            for bid, _typ, text in blocks:
                for m in _INJECTION.finditer(text):
                    target = _words(m.group(1))
                    for f in fields:
                        if target and (target in _words(_field_key(f)) or _words(_field_key(f)) in target):
                            out[_field_key(f)] = {
                                "value": parse_value(str(f["type"]), m.group(2), f) or m.group(2),
                                "block_id": bid,
                                "quote": m.group(0),
                                "self_confidence": 0.99,
                            }
        return {"fields": out}

    def _extract_scalars(
        self, request: LLMRequest, fields: list[dict[str, Any]], blocks: list[tuple[str, str, str]]
    ) -> dict[str, Any]:
        lines: list[tuple[str, str]] = [
            (bid, ln) for bid, _t, text in blocks for ln in text.split("\n") if ln.strip()
        ]
        cands: list[tuple[int, int, int, int, str, re.Match[str]]] = []
        for fi, f in enumerate(fields):
            for rank, lab in enumerate(labels_for(f)):
                pat = re.compile(
                    rf"(?<![a-z0-9]){re.escape(lab)}(?![a-z0-9])\s*(?:no\.?|number|#)?\s*[:\-–#|=]*\s*", re.I
                )
                for li, (_bid, ln) in enumerate(lines):
                    m = pat.search(ln)
                    if m:
                        cands.append((-len(lab), rank, li, fi, lab, m))
        cands.sort(key=lambda c: c[:4])
        claimed: dict[int, list[tuple[int, int]]] = {}
        out: dict[str, Any] = {_field_key(f): None for f in fields}
        for neg_len, rank, li, fi, _lab, m in cands:
            f = fields[fi]
            key = _field_key(f)
            if out[key] is not None:
                continue
            spans = claimed.setdefault(li, [])
            if any(m.start() < e and s < m.end() for s, e in spans):
                continue
            bid, ln = lines[li]
            rest = ln[m.end() :]
            nxt = min((s for s, _e in spans if s >= m.end()), default=None)
            if nxt is not None:
                rest = ln[m.end() : nxt]
            value = parse_value(str(f["type"]), rest, f)
            if value is None:
                continue
            spans.append((m.start(), m.end()))
            conf = 0.93 if rank == 0 or -neg_len >= 8 else 0.86
            item: dict[str, Any] | None = {
                "value": value,
                "block_id": bid,
                "quote": ln.strip(),
                "self_confidence": conf,
            }
            fault = self._faulty(request, bid, key)
            if fault == 0:
                item = None
            elif fault == 1 and isinstance(value, int | float):
                others = [
                    (b2, l2, v2)
                    for b2, l2 in lines
                    for v2 in [parse_value("number", l2.split(":")[-1], f)]
                    if v2 is not None and v2 != value
                ]
                if others:
                    b2, l2, v2 = others[fi % len(others)]
                    item = {"value": v2, "block_id": b2, "quote": l2.strip(), "self_confidence": 0.9}
            elif fault == 2:
                item = {
                    "value": value,
                    "block_id": bid,
                    "quote": f"{key} is certainly {value} per our records",
                    "self_confidence": 0.97,
                }
            out[key] = item
        return out

    def _extract_rows(
        self, request: LLMRequest, fields: list[dict[str, Any]], blocks: list[tuple[str, str, str]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for bid, _typ, text in blocks:
            table = [ln for ln in text.split("\n") if ln.strip().startswith("|")]
            if len(table) < 2:
                continue
            header = [_words(c) for c in _split_row(table[0])]
            colmap: dict[str, int] = {}
            for f in fields:
                best, best_score = None, 0.0
                for rank, lab in enumerate(labels_for(f)):
                    for ci, h in enumerate(header):
                        if ci in colmap.values() or not h:
                            continue
                        score = _label_score(h, lab) - 0.01 * rank
                        if score > best_score:
                            best, best_score = ci, score
                if best is not None and best_score >= 0.74:
                    colmap[_field_key(f)] = best
            if len(colmap) < max(1, len(fields) // 2):
                continue
            for ln in table[1:]:
                cells = _split_row(ln)
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells) or len(cells) < len(header):
                    continue
                if re.search(
                    r"\b(sub\s*total|grand total|total|cgst|sgst|igst)\b", cells[0].lower()
                ) or not any(cells):
                    continue
                row: dict[str, Any] = {}
                for f in fields:
                    key = _field_key(f)
                    if key not in colmap:
                        continue
                    raw = cells[colmap[key]]
                    value = parse_value(str(f["type"]), raw, f)
                    if value is not None:
                        if self._faulty(request, bid, f"{key}{len(rows)}") == 0:
                            continue
                        row[key] = {
                            "value": value,
                            "block_id": bid,
                            "quote": ln.strip(),
                            "self_confidence": 0.92,
                        }
                if row:
                    rows.append(row)
        return rows
