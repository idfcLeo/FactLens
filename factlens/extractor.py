from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import fitz  # PyMuPDF
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Match numeric values (allow unformatted multi-digit integers without truncating to 3 digits)
NUMBER = re.compile(r"(?<![\w.])(?:₹|\$|€|£)?\s?\d+(?:,\d{3})*(?:\.\d+)?\s?(?:%|percent|crore|million|billion|bn|mn|lakh)?\b", re.I)
YEAR = re.compile(r"\b(?:FY\s?)?20\d{2}(?:[-–/]\d{2,4})?\b|\bQ[1-4]\s*(?:FY\s*)?\d{2,4}\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
          "january", "february", "march", "april", "june", "july", "august", "september", "october", "november", "december"}
STOP = frozenset(
    "the a an and or of in on at to for from with by is are was were be been this that as it its their our which into than "
    "report financial during page figure table view overview source total value statement".split()
) | MONTHS


@dataclass
class Evidence:
    document_id: str
    document_name: str
    page: int
    excerpt: str


@dataclass
class Fact:
    id: str
    claim: str
    kind: str
    value: Optional[str]
    period: Optional[str]
    subject: str
    confidence: float
    evidence: Evidence
    normalized_value: Optional[float] = None
    currency: Optional[str] = None

    def json(self):
        d = asdict(self)
        d["evidence"] = asdict(self.evidence)
        return d


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-zA-Z]{3,}", text.lower()) if x not in STOP}


def _subject(sentence: str) -> str:
    words = [w for w in _tokens(sentence) if w not in {"report", "financial", "during", "statement", "table", "column", "row"}]
    return " ".join(words[:6]) or "unclassified statement"


def _extract_currency(value_str: str) -> Optional[str]:
    v = value_str.lower()
    if "$" in v:
        return "USD"
    if "₹" in v or "rs" in v or "inr" in v:
        return "INR"
    if "€" in v:
        return "EUR"
    if "£" in v:
        return "GBP"
    return None


def _normalized_number(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    raw = re.sub(r"[^\d.]", "", value.replace(",", ""))
    try:
        n = float(raw)
    except ValueError:
        return None
    unit = value.lower()
    if "crore" in unit:
        n *= 10_000_000
    elif "lakh" in unit:
        n *= 100_000
    elif "million" in unit or " mn" in unit:
        n *= 1_000_000
    elif "billion" in unit or " bn" in unit:
        n *= 1_000_000_000
    return n


def _meaningful_number(match: re.Match[str], sentence: str) -> bool:
    value = _clean(match.group(0))
    # Ignore standalone 4-digit years like 2024 or 2021-22
    if re.fullmatch(r"20\d{2}", value) or re.fullmatch(r"20\d{2}[-/]\d{2,4}", value):
        return False
    # Ignore day numbers in date strings e.g. "Dec 2021"
    if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+" + re.escape(value), sentence, re.I):
        return False
    # Ignore footnote numbers at start of text line e.g. "41 World Economic Outlook"
    if match.start() == 0 and re.match(r"^\d{1,2}\s+[A-Z]", sentence):
        return False
    return True


def _best_numeric_match(matches: List[re.Match[str]]) -> re.Match[str]:
    def quality(match: re.Match[str]) -> Tuple[int, int]:
        val = match.group(0).lower()
        has_unit = int(any(u in val for u in ("₹", "$", "€", "£", "%", "percent", "crore", "million", "billion", "lakh", "bn", "mn")))
        digits = len(re.sub(r"\D", "", val))
        return has_unit, digits
    return max(matches, key=quality)


def _sentence_facts(document_id: str, name: str, page: int, text: str, start: int) -> Iterable[Fact]:
    excerpt = _clean(text)
    if len(excerpt) < 25 or len(excerpt) > 700:
        return
    evidence = Evidence(document_id, name, page, excerpt)
    period_match = YEAR.search(excerpt)
    period = period_match.group(0) if period_match else None
    matches = [m for m in NUMBER.finditer(excerpt) if _meaningful_number(m, excerpt)]
    if matches:
        match = _best_numeric_match(matches)
        val_str = _clean(match.group(0))
        norm_val = _normalized_number(val_str)
        curr = _extract_currency(val_str)
        yield Fact(
            id=f"{document_id}:{page}:{start}:n", claim=excerpt, kind="numeric",
            value=val_str, period=period, subject=_subject(excerpt),
            confidence=0.82, evidence=evidence, normalized_value=norm_val, currency=curr
        )
    elif len(_tokens(excerpt)) >= 5:
        yield Fact(
            id=f"{document_id}:{page}:{start}:s", claim=excerpt, kind="semantic",
            value=None, period=period, subject=_subject(excerpt), confidence=0.62, evidence=evidence
        )


def _table_facts(document_id: str, name: str, page: int, doc: fitz.Document, page_num: int) -> List[Fact]:
    facts = []
    try:
        fitz_page = doc[page_num - 1]
        tabs = fitz_page.find_tables()
        for tab_idx, tab in enumerate(tabs):
            df = tab.extract()
            if not df or len(df) < 2:
                continue
            headers = [str(cell or "").strip() for cell in df[0]]
            for row_idx, row in enumerate(df[1:], start=1):
                row_cells = [str(cell or "").strip() for cell in row]
                row_label = _clean(row_cells[0]) if row_cells else ""
                # Skip rows with no text label or row labels that lack real text tokens
                if not row_label or not _tokens(row_label):
                    continue
                for col_idx, cell_val in enumerate(row_cells[1:], start=1):
                    col_header = _clean(headers[col_idx]) if col_idx < len(headers) else ""
                    cell_val = _clean(cell_val)
                    if not cell_val or len(cell_val) < 2:
                        continue
                    # Skip cells that are just date headers e.g. 2021, 2021/22
                    if re.fullmatch(r"(?:FY\s*)?20\d{2}(?:[-/]\d{2,4})?", cell_val, re.I):
                        continue
                    matches = [m for m in NUMBER.finditer(cell_val) if _meaningful_number(m, cell_val)]
                    if matches:
                        match = _best_numeric_match(matches)
                        val_str = _clean(match.group(0))
                        claim_text = _clean(f"{row_label} ({col_header}): {cell_val}") if col_header else _clean(f"{row_label}: {cell_val}")
                        period_match = YEAR.search(col_header) or YEAR.search(row_label) or YEAR.search(claim_text)
                        period = period_match.group(0) if period_match else None
                        evidence = Evidence(document_id, name, page, claim_text)
                        facts.append(Fact(
                            id=f"{document_id}:{page}:t{tab_idx}r{row_idx}c{col_idx}:n",
                            claim=claim_text, kind="numeric", value=val_str, period=period,
                            subject=_subject(f"{row_label} {col_header}"), confidence=0.88,
                            evidence=evidence, normalized_value=_normalized_number(val_str),
                            currency=_extract_currency(val_str)
                        ))
    except Exception:
        pass
    return facts


def extract_pdf(path: str | Path, document_id: str | None = None) -> list[Fact]:
    path = Path(path)
    document_id = document_id or path.stem
    facts: list[Fact] = []
    
    doc = fitz.open(str(path))
    try:
        for page_number, fitz_page in enumerate(doc, start=1):
            page_text = fitz_page.get_text("text", sort=True) or ""
            offset = 0
            for sentence in SENTENCE.split(page_text):
                facts.extend(_sentence_facts(document_id, path.name, page_number, sentence, offset) or [])
                offset += len(sentence) + 1
                
            t_facts = _table_facts(document_id, path.name, page_number, doc, page_number)
            facts.extend(t_facts)
    finally:
        doc.close()

    return facts


def relate(facts: list[Fact]) -> list[dict]:
    if not facts:
        return []

    claims = [f.claim for f in facts]
    vectorizer = TfidfVectorizer(stop_words=list(STOP), min_df=1, token_pattern=r"(?u)\b[a-zA-Z]{3,}\b")
    try:
        tfidf_matrix = vectorizer.fit_transform(claims)
        sim_matrix = cosine_similarity(tfidf_matrix)
    except Exception:
        sim_matrix = None

    relations: list[dict] = []
    n_facts = len(facts)

    for i in range(n_facts):
        left = facts[i]
        for j in range(i + 1, n_facts):
            right = facts[j]
            if left.evidence.document_id == right.evidence.document_id:
                continue

            if sim_matrix is not None:
                sim_score = float(sim_matrix[i, j])
            else:
                tokens_l = _tokens(left.claim)
                tokens_r = _tokens(right.claim)
                sim_score = len(tokens_l & tokens_r) / max(1, len(tokens_l | tokens_r))

            same_period = left.period and right.period and left.period == right.period
            ln, rn = left.normalized_value, right.normalized_value

            # Same period numeric evaluation: requires shared tokens and reasonable similarity
            if ln is not None and rn is not None and same_period and (sim_score >= 0.25 or bool(_tokens(left.claim) & _tokens(right.claim))):
                mult = 1.0
                if left.currency == "USD" and right.currency == "INR":
                    mult = 83.0
                elif left.currency == "INR" and right.currency == "USD":
                    mult = 1 / 83.0

                diff_pct = abs((ln * mult) - rn) / max(abs(ln * mult), abs(rn), 1.0)
                close = diff_pct < 0.03

                relation = "corroborates" if close else "contradicts"
                reason = "Same period and similar claim language; values are " + ("within 3.0%." if close else f"materially different ({diff_pct:.1%} variance).")
                confidence = round(min(0.95, 0.50 + sim_score * 0.45), 2)

            elif sim_score < 0.32:
                continue
            
            elif left.period and right.period and left.period != right.period and (sim_score >= 0.45 or (left.subject == right.subject and left.subject != "unclassified statement")):
                relation = "reconciles"
                reason = f"The evidence refers to different reporting periods ({left.period} vs {right.period}), so the apparent variation is contextual."
                confidence = round(min(0.90, 0.40 + sim_score * 0.50), 2)

            elif sim_score >= 0.58:
                relation = "corroborates"
                reason = "Different wording has substantial semantic topic overlap; review the linked receipts to verify identity."
                confidence = round(min(0.90, 0.35 + sim_score * 0.55), 2)
            else:
                continue

            relations.append({
                "id": f"r:{left.id}:{right.id}",
                "type": relation,
                "reason": reason,
                "confidence": confidence,
                "similarity_score": round(sim_score, 3),
                "left": left.json(),
                "right": right.json()
            })

    # Strict deduplication: collapse redundant comparisons for the same document pair, relation type, subject, period, and value
    grouped: dict[tuple, dict] = {}
    for relation in relations:
        left_f = relation["left"]
        right_f = relation["right"]
        l_doc = left_f["evidence"]["document_id"]
        r_doc = right_f["evidence"]["document_id"]
        
        subj_key = left_f.get("subject") if left_f.get("subject") != "unclassified statement" else right_f.get("subject")
        val_key = left_f.get("normalized_value") or left_f.get("value")
        period_key = left_f.get("period") or right_f.get("period")
        
        pair_key = (
            relation["type"],
            tuple(sorted([l_doc, r_doc])),
            period_key,
            val_key,
            subj_key
        )
        if pair_key not in grouped or relation["confidence"] > grouped[pair_key]["confidence"]:
            grouped[pair_key] = relation

    return sorted(grouped.values(), key=lambda x: x["confidence"], reverse=True)[:100]
