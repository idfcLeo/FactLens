# FactLens

FactLens is an evidence-first fact knowledge layer for PDFs. It favors a small, inspectable pipeline over an opaque answer generator: every candidate claim links back to a verbatim page excerpt, and every cross-document relationship states why it was suggested.

## Setup and run instructions

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8000`, upload one or more text-based PDFs, and inspect the relationship cards. The supplied source packets in `starter-datasets/` are suitable test inputs. Reset clears the local knowledge store, not the original documents.

Run the small relationship test suite with:

```powershell
python -m pytest
```

## Video demo

Record a 3-minute-or-less walkthrough after running locally: upload two overlapping PDFs, inspect a relationship card and its two page citations, show each relationship filter, then show the extraction watchlist. A video link has intentionally not been invented for this repository.

## Approach

### Pipeline

1. **Ingest** - accept arbitrary PDF uploads and extract text per page with `pypdf`.
2. **Propose facts** - split page text into sentences. Numeric statements become numeric candidates (value and reporting period are retained); sufficiently substantial non-numeric statements become semantic candidates.
3. **Ground** - store the original filename, page number, and exact text excerpt with each candidate fact.
4. **Compare** - only compare claims in different documents with meaningful token overlap. Same-period numeric candidates are corroborated when values are within 2.5%, otherwise marked as a likely contradiction. Different reporting periods become a reconciliation rather than a contradiction. High-overlap prose can be a cautious corroboration.
5. **Review** - the UI displays both source receipts, the heuristic's reasoning, and its confidence signal. It never collapses two pieces of evidence into a single asserted truth.

This design is deliberately schema-light. It does not know that a document is about Delhivery, India, revenue, or any particular supplied filename. A subject label is merely a compact comparison aid derived from content tokens. The stored JSON knowledge layer makes new uploads incremental; prior extraction is not rerun.

### Four required cases

The relationship filters expose the first three cases when the uploaded documents contain comparable evidence:

| Required case | FactLens behavior |
| --- | --- |
| Corroborated fact | Shows a `CORROBORATES` card with both excerpts. |
| Genuine/likely contradiction | Shows a `CONTRADICTS` card for materially different numeric values in the same period. |
| Apparent contradiction explained by context | Shows a `RECONCILES` card when similar claims name different reporting periods. |
| Extraction/reasoning failure | The watchlist reports a textless/scanned PDF extraction failure and states the missing capability. |

## Trade-offs

- Token overlap is interpretable and dependency-light, but is weaker than entity resolution or semantic embeddings. It intentionally produces conservative candidate links, not definitive identity judgments.
- PDF text extraction is reliable for born-digital PDFs but not scanned/image-only PDFs. OCR is the next highest-value improvement.
- Numeric normalization supports common magnitude words, not full table parsing or unit ontology. Values that look alike but use incompatible units may still need reviewer judgment.
- The app persists locally in JSON for transparency and easy setup. SQLite plus a background ingestion queue would be the next practical production step.

## AI tools used

This prototype uses deterministic local heuristics, not a hosted LLM. That was a conscious choice: the evidence and comparison rationale stay reproducible without credentials. A future optional LLM stage could propose entity aliases and semantic links, while keeping the existing evidence-first reviewer workflow and confidence boundaries.

## Limitations and next steps

Add OCR for scans, table-aware extraction, unit conversion, embeddings with calibrated thresholds, reviewer feedback loops, and a durable database/search index. I would also add a PDF-page preview beside each excerpt and an evaluation set with labeled corroboration, contradiction, reconciliation, and failure examples.
