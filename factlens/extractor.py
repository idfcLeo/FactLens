from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader

NUMBER = re.compile(r"(?<![\w.])(?:₹|\$|€|£)?\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(?:%|percent|crore|million|billion|bn|mn|lakh)?", re.I)
YEAR = re.compile(r"\b(?:FY\s?)?20\d{2}(?:[-–]\d{2,4})?\b|\bQ[1-4]\s*(?:FY\s*)?\d{2,4}\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n{2,}")
STOP = frozenset("""the a an and or of in on at to for from with by is are was were be been this that as it its their our which were into than
jan january feb february mar march apr april may jun june jul july aug august sep sept september oct october nov november dec december""".split())
MONTH = re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", re.I)
FOOTNOTE = re.compile(r"^\s*(?:\[)?\d{1,3}(?:\]|[.)])?\s+[A-Z]")
METRIC_TERMS = frozenset("revenue sales profit income ebitda margin growth inflation gdp output debt assets liabilities equity earnings volume shipments customers employees headcount expenditure capex investment cash flow dividend tax interest rate".split())


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
    value: str | None
    period: str | None
    subject: str
    confidence: float
    evidence: Evidence

    def json(self):
        return {**asdict(self), "evidence": asdict(self.evidence)}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-zA-Z]{3,}", text.lower()) if x not in STOP}


def _subject(sentence: str) -> str:
    # A concise, stable comparison label without pretending to understand a schema.
    words = [w for w in _tokens(sentence) if w not in {"report", "financial", "during"}]
    return " ".join(words[:6]) or "unclassified statement"


def _meaningful_number(match: re.Match[str], sentence: str) -> bool:
    """Ignore years and day numbers from dates; they are context, not measured facts."""
    value = _clean(match.group(0))
    if re.fullmatch(r"20\d{2}", value):
        return False
    # Bibliography and footnote markers ("41 World Bank ...") are identifiers,
    # not measurements. Do not turn their leading number into a numeric fact.
    if match.start() == 0 and FOOTNOTE.match(sentence):
        return False
    # e.g. the "11" in "May 11, 2024".
    return not MONTH.search(sentence[max(0, match.start() - 14):match.start()])


def _best_numeric_match(matches: list[re.Match[str]]) -> re.Match[str]:
    """Keep one representative value per sentence so dense tables do not fan out."""
    def quality(match: re.Match[str]) -> tuple[int, int]:
        value = match.group(0).lower()
        has_unit = int(any(unit in value for unit in ("₹", "$", "€", "£", "%", "percent", "crore", "million", "billion", "lakh", " bn", " mn")))
        digits = len(re.sub(r"\D", "", value))
        return has_unit, digits
    return max(matches, key=quality)


def _sentence_facts(document_id: str, name: str, page: int, text: str, start: int) -> Iterable[Fact]:
    excerpt = _clean(text)
    if len(excerpt) < 25 or len(excerpt) > 700:
        return
    evidence = Evidence(document_id, name, page, excerpt)
    period = (YEAR.search(excerpt).group(0) if YEAR.search(excerpt) else None)
    matches = [match for match in NUMBER.finditer(excerpt) if _meaningful_number(match, excerpt)]
    if matches:
        # PDF table extraction often returns a whole row as one sentence. Treating every
        # cell as a separate claim creates a Cartesian product of duplicate comparisons.
        match = _best_numeric_match(matches)
        yield Fact(
            id=f"{document_id}:{page}:{start}:n", claim=excerpt, kind="numeric",
            value=_clean(match.group(0)), period=period, subject=_subject(excerpt),
            confidence=0.78, evidence=evidence,
        )
    elif len(_tokens(excerpt)) >= 5:
        yield Fact(
            id=f"{document_id}:{page}:{start}:s", claim=excerpt, kind="semantic",
            value=None, period=period, subject=_subject(excerpt), confidence=0.58, evidence=evidence,
        )


def extract_pdf(path: str | Path, document_id: str | None = None) -> list[Fact]:
    """Extract candidate facts with page-level, verbatim evidence from any text PDF."""
    path = Path(path)
    document_id = document_id or path.stem
    facts: list[Fact] = []
    reader = PdfReader(str(path))
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text(extraction_mode="layout") or ""
        offset = 0
        for sentence in SENTENCE.split(page_text):
            facts.extend(_sentence_facts(document_id, path.name, page_number, sentence, offset) or [])
            offset += len(sentence) + 1
    return facts


def similarity(left: Fact, right: Fact) -> float:
    a, b = _tokens(left.claim), _tokens(right.claim)
    return len(a & b) / max(1, len(a | b))


def _normalized_number(value: str | None) -> float | None:
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
    elif "million" in unit or " mn" in unit:
        n *= 1_000_000
    elif "billion" in unit or " bn" in unit:
        n *= 1_000_000_000
    return n


def relate(facts: list[Fact]) -> list[dict]:
    """Compare evidence conservatively. Relations preserve ambiguity instead of merging facts."""
    relations: list[dict] = []
    token_sets = [_tokens(fact.claim) for fact in facts]
    inverted: dict[str, list[int]] = defaultdict(list)
    for index, tokens in enumerate(token_sets):
        for token in tokens:
            inverted[token].append(index)

    # Only compare candidates that share an uncommon meaningful term.
    # This avoids the old all-against-all scan as document collections grow.
    overlap_counts: dict[tuple[int, int], int] = defaultdict(int)
    for postings in inverted.values():
        if len(postings) > 80:  # boilerplate such as "financial" is not a useful bridge
            continue
        for offset, left_index in enumerate(postings):
            left_doc = facts[left_index].evidence.document_id
            for right_index in postings[offset + 1:]:
                if left_doc != facts[right_index].evidence.document_id:
                    overlap_counts[(left_index, right_index)] += 1

    for (left_index, right_index), overlap in overlap_counts.items():
        if overlap < 1:
            continue
        left, right = facts[left_index], facts[right_index]
        score = overlap / max(1, len(token_sets[left_index] | token_sets[right_index]))
        if score < 0.24:
            continue
        same_period = left.period and left.period == right.period
        ln, rn = _normalized_number(left.value), _normalized_number(right.value)
        if ln is not None and rn is not None and same_period:
            close = abs(ln - rn) / max(abs(ln), abs(rn), 1) < 0.025
            relation = "corroborates" if close else "contradicts"
            reason = "Same period and similar claim language; values are " + ("within 2.5%." if close else "materially different.")
        elif left.period and right.period and left.period != right.period:
            shared_metrics = (token_sets[left_index] & token_sets[right_index]) & METRIC_TERMS
            compatible_values = ln is not None and rn is not None and abs(ln - rn) / max(abs(ln), abs(rn), 1) < 0.025
            if not shared_metrics and not compatible_values:
                continue
            relation, reason = "reconciles", "The evidence refers to different reporting periods, and the matched metric or value makes the apparent conflict contextual."
        elif score >= 0.42:
            relation, reason = "corroborates", "Different wording has substantial topic overlap; review the linked evidence before treating as identical."
        else:
            continue
        relations.append({"id": f"r:{left.id}:{right.id}", "type": relation, "reason": reason,
                          "confidence": round(min(.95, .45 + score), 2), "left": left.json(), "right": right.json()})
    # Multiple values can be recovered from one table row. The reviewer needs one card
    # per source-excerpt pair, not a card for every possible pair of table cells.
    grouped: dict[tuple, dict] = {}
    for relation in relations:
        left, right = relation["left"]["evidence"], relation["right"]["evidence"]
        pair = tuple(sorted(((left["document_id"], left["page"], left["excerpt"]),
                             (right["document_id"], right["page"], right["excerpt"]))))
        key = (relation["type"], pair)
        if key not in grouped or relation["confidence"] > grouped[key]["confidence"]:
            grouped[key] = relation
    return sorted(grouped.values(), key=lambda x: x["confidence"], reverse=True)[:100]
