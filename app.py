from __future__ import annotations

import json
import hashlib
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from factlens.extractor import extract_pdf, relate

ROOT = Path(__file__).parent
UPLOADS = ROOT / "data" / "uploads"
STORE = ROOT / "data" / "knowledge.json"
EXTRACTOR_VERSION = 3
UPLOADS.mkdir(parents=True, exist_ok=True)
app = Flask(
    __name__,
    template_folder=str(ROOT / "factlens" / "templates"),
    static_folder=str(ROOT / "factlens" / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024


def read_store():
    return json.loads(STORE.read_text()) if STORE.exists() else {"documents": [], "facts": [], "relations": [], "diagnostics": []}


def write_store(data):
    STORE.parent.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2))


def rebuild(data):
    """Regenerate facts using the current rules and collapse byte-identical uploads."""
    unique: dict[str, tuple[dict, Path]] = {}
    for document in data.get("documents", []):
        matches = list(UPLOADS.glob(f"{document['id']}-*"))
        if not matches:
            continue
        source = matches[0]
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        # Prefer the more descriptive original filename when the same PDF was uploaded twice.
        if digest not in unique or len(document["name"]) > len(unique[digest][0]["name"]):
            unique[digest] = (document, source)

    documents, facts, diagnostics = [], [], []
    for document, source in unique.values():
        extracted = extract_pdf(source, document["id"])
        documents.append({"id": document["id"], "name": document["name"], "facts": len(extracted)})
        facts.extend(fact.json() for fact in extracted)
        if not extracted:
            diagnostics.append({"type": "extraction_failure", "document": document["name"],
                "message": "No text facts were found. This is likely a scanned PDF; OCR is not enabled in this prototype."})
    from factlens.extractor import Evidence, Fact
    objects = [Fact(**{**fact, "evidence": Evidence(**fact["evidence"])}) for fact in facts]
    return {"extractor_version": EXTRACTOR_VERSION, "documents": documents, "facts": facts,
            "relations": relate(objects), "diagnostics": diagnostics}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/knowledge")
def knowledge():
    data = read_store()
    if data.get("extractor_version") != EXTRACTOR_VERSION:
        data = rebuild(data)
        write_store(data)
    return jsonify(data)


@app.post("/api/documents")
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify(error="Choose one or more PDF files."), 400
    data = read_store()
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            return jsonify(error=f"{file.filename or 'File'} is not a PDF."), 400
        doc_id = uuid.uuid4().hex[:10]
        filename = f"{doc_id}-{Path(file.filename).name}"
        target = UPLOADS / filename
        file.save(target)
        try:
            facts = extract_pdf(target, doc_id)
        except Exception as exc:
            target.unlink(missing_ok=True)
            return jsonify(error=f"Could not read {file.filename}: {exc}"), 422
        data["documents"].append({"id": doc_id, "name": file.filename, "facts": len(facts)})
        data["facts"].extend(f.json() for f in facts)
        if not facts:
            data["diagnostics"].append({"type": "extraction_failure", "document": file.filename,
                "message": "No text facts were found. This is likely a scanned PDF; OCR is not enabled in this prototype."})
    # Rehydrate enough structure for comparison, keeping storage JSON-friendly.
    from factlens.extractor import Evidence, Fact
    objects = [Fact(**{**f, "evidence": Evidence(**f["evidence"])}) for f in data["facts"]]
    data["relations"] = relate(objects)
    data["extractor_version"] = EXTRACTOR_VERSION
    write_store(data)
    return jsonify(data), 201


@app.post("/api/reset")
def reset():
    write_store({"extractor_version": EXTRACTOR_VERSION, "documents": [], "facts": [], "relations": [], "diagnostics": []})
    return ("", 204)


if __name__ == "__main__":
    app.run(debug=True, port=8000)
