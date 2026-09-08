from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import fitz  # PyMuPDF
from sklearn.feature_extraction.text import TfidfVectorizer

NUMBER = re.compile(r"(?<![\w.])(?:₹|\$|€|£)?\s?\d+(?:,\d{3})*(?:\.\d+)?\s?(?:%|percent|crore|million|billion|bn|mn|lakh)?\b", re.I)
YEAR = re.compile(r"\b(?:FY\s*)?(?:20\d{2}|\d{2})(?:[-–/]\d{2,4})?\b|\bQ[1-4]\s*(?:FY\s*)?\d{2,4}\b", re.I)
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


def _normalize_period_year(period: Optional[str]) -> Optional[int]:
    if not period:
        return None
    m = re.search(r"20\d{2}", period)
    if m:
        return int(m.group(0))
    m2 = re.search(r"\b(?:FY\s*)?(\d{2})\b", period, re.I)
    if m2:
        yr = int(m2.group(1))
        if 0 <= yr <= 50:
            return 2000 + yr
    return None


def _meaningful_number(match: re.Match[str], sentence: str) -> bool:
    value = _clean(match.group(0))
    if re.fullmatch(r"20\d{2}", value) or re.fullmatch(r"20\d{2}[-/]\d{2,4}", value):
        return False
    if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+" + re.escape(value), sentence, re.I):
        return False
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
    if len(excerpt) < 25 or len(excerpt) > 500:
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


def extract_pdf(path: str | Path, document_id: str | None = None) -> list[Fact]:
    """Fast layout block extraction, sampling high-quality candidate facts."""
    path = Path(path)
    document_id = document_id or path.stem
    facts: list[Fact] = []
    
    doc = fitz.open(str(path))
    try:
        for page_number, fitz_page in enumerate(doc, start=1):
            blocks = fitz_page.get_text("blocks")
            for block_idx, b in enumerate(blocks):
                text = b[4] if len(b) > 4 else ""
                if not text:
                    continue
                offset = 0
                for sentence in SENTENCE.split(text):
                    facts.extend(_sentence_facts(document_id, path.name, page_number, sentence, offset) or [])
                    offset += len(sentence) + 1
    finally:
        doc.close()

    # Cap to top 250 facts per document to prevent combinatorial explosion
    return facts[:250]


def relate(facts: list[Fact]) -> list[dict]:
    """Ultra-fast relation classifier using token posting list indexing & sparse TF-IDF vectors."""
    if not facts:
        return []

    # 1. Build inverted token index for lightning fast candidate pair filtering
    token_map = defaultdict(list)
    for idx, f in enumerate(facts):
        for tok in _tokens(f.claim):
            token_map[tok].append(idx)

    candidate_pairs = set()
    for tok, indices in token_map.items():
        if len(indices) > 60:  # Skip common boilerplate terms that appear in 60+ facts
            continue
        n_idx = len(indices)
        for i in range(n_idx):
            idx_i = indices[i]
            doc_i = facts[idx_i].evidence.document_id
            for j in range(i + 1, n_idx):
                idx_j = indices[j]
                if doc_i != facts[idx_j].evidence.document_id:
                    pair = (min(idx_i, idx_j), max(idx_i, idx_j))
                    candidate_pairs.add(pair)

    if not candidate_pairs:
        return []

    # 2. Fit TF-IDF matrix across claims
    claims = [f.claim for f in facts]
    try:
        vectorizer = TfidfVectorizer(stop_words=list(STOP), min_df=1, token_pattern=r"(?u)\b[a-zA-Z]{3,}\b")
        tfidf_matrix = vectorizer.fit_transform(claims)
    except Exception:
        tfidf_matrix = None

    relations: list[dict] = []

    # 3. Evaluate ONLY relevant candidate pairs (bypassing N*N loop)
    for i, j in candidate_pairs:
        left = facts[i]
        right = facts[j]

        if tfidf_matrix is not None:
            sim_score = float((tfidf_matrix[i] * tfidf_matrix[j].T).toarray()[0, 0])
        else:
            tokens_l = _tokens(left.claim)
            tokens_r = _tokens(right.claim)
            sim_score = len(tokens_l & tokens_r) / max(1, len(tokens_l | tokens_r))

        y1 = _normalize_period_year(left.period)
        y2 = _normalize_period_year(right.period)
        same_period = (y1 is not None and y2 is not None and y1 == y2)
        different_period = (y1 is not None and y2 is not None and y1 != y2)

        ln, rn = left.normalized_value, right.normalized_value
        has_numbers = (ln is not None and rn is not None)

        if has_numbers and same_period and (sim_score >= 0.15 or bool(_tokens(left.claim) & _tokens(right.claim))):
            mult = 1.0
            if left.currency == "USD" and right.currency == "INR":
                mult = 83.0
            elif left.currency == "INR" and right.currency == "USD":
                mult = 1 / 83.0

            diff_pct = abs((ln * mult) - rn) / max(abs(ln * mult), abs(rn), 1.0)
            close = diff_pct < 0.03

            relation = "corroborates" if close else "contradicts"
            reason = "Same period and similar claim language; values are " + ("within 3.0%." if close else f"materially different ({diff_pct:.1%} variance: {left.value} vs {right.value}).")
            confidence = round(min(0.95, 0.50 + sim_score * 0.45), 2)

        elif has_numbers and different_period and (sim_score >= 0.15 or bool(_tokens(left.claim) & _tokens(right.claim))):
            relation = "reconciles"
            reason = f"The evidence refers to different reporting periods ({left.period or y1} vs {right.period or y2}), so the apparent variation is contextual."
            confidence = round(min(0.95, 0.55 + sim_score * 0.40), 2)

        elif sim_score < 0.28:
            continue

        elif different_period and (sim_score >= 0.35 or (left.subject == right.subject and left.subject != "unclassified statement")):
            relation = "reconciles"
            reason = f"The evidence refers to different reporting periods ({left.period or y1} vs {right.period or y2}), so the apparent variation is contextual."
            confidence = round(min(0.95, 0.50 + sim_score * 0.45), 2)

        elif sim_score >= 0.50:
            relation = "corroborates"
            reason = "Different wording has substantial semantic topic overlap; review the linked receipts to verify identity."
            confidence = round(min(0.95, 0.40 + sim_score * 0.50), 2)
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

    all_rels = list(grouped.values())
    
    corrob_rels = sorted([r for r in all_rels if r["type"] == "corroborates"], key=lambda x: x["confidence"], reverse=True)
    contra_rels = sorted([r for r in all_rels if r["type"] == "contradicts"], key=lambda x: x["confidence"], reverse=True)
    recon_rels = sorted([r for r in all_rels if r["type"] == "reconciles"], key=lambda x: x["confidence"], reverse=True)

    result = recon_rels[:40] + contra_rels[:30] + corrob_rels[:50]
    return sorted(result, key=lambda x: x["confidence"], reverse=True)[:100]
