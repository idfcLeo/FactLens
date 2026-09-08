from __future__ import annotations

import hashlib
import io
import uuid
from pathlib import Path

import fitz  # PyMuPDF
from flask import Flask, jsonify, render_template, request, send_file
from factlens.db import Database
from factlens.extractor import Evidence, Fact, extract_pdf, relate

ROOT = Path(__file__).parent
UPLOADS = ROOT / "data" / "uploads"
EXTRACTOR_VERSION = 3
UPLOADS.mkdir(parents=True, exist_ok=True)

db = Database()

app = Flask(
    __name__,
    template_folder=str(ROOT / "factlens" / "templates"),
    static_folder=str(ROOT / "factlens" / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 35 * 1024 * 1024


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/knowledge")
def knowledge():
    return jsonify(db.get_knowledge())


@app.get("/api/documents/<doc_id>/pages/<int:page_num>/preview")
def page_preview(doc_id: str, page_num: int):
    """Render a rendered PNG image of a PDF page for side-by-side inspection."""
    matches = list(UPLOADS.glob(f"{doc_id}-*"))
    if not matches:
        return jsonify(error="Document not found."), 404
    pdf_path = matches[0]

    try:
        doc = fitz.open(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return jsonify(error="Page number out of range."), 400
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(img_bytes), mimetype="image/png")
    except Exception as exc:
        return jsonify(error=f"Could not render page: {exc}"), 500


@app.post("/api/documents")
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify(error="Choose one or more PDF files."), 400

    existing = db.get_knowledge()
    documents = existing.get("documents", [])
    facts_list = existing.get("facts", [])
    diagnostics = existing.get("diagnostics", [])

    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            return jsonify(error=f"{file.filename or 'File'} is not a PDF."), 400
        doc_id = uuid.uuid4().hex[:10]
        filename = f"{doc_id}-{Path(file.filename).name}"
        target = UPLOADS / filename
        file.save(target)

        try:
            extracted = extract_pdf(target, doc_id)
        except Exception as exc:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return jsonify(error=f"Could not read {file.filename}: {exc}"), 422

        documents.append({"id": doc_id, "name": file.filename, "facts": len(extracted)})
        facts_list.extend(f.json() for f in extracted)

        if not extracted:
            diagnostics.append({
                "type": "extraction_failure",
                "document": file.filename,
                "message": "No text facts or tables were found. This may be a scanned or image-only PDF."
            })

    # Rehydrate Fact objects for relationship computation
    objects = []
    for f in facts_list:
        ev = Evidence(**f["evidence"])
        f_copy = {k: v for k, v in f.items() if k != "evidence"}
        objects.append(Fact(**f_copy, evidence=ev))

    relations = relate(objects)
    db.save_knowledge(documents, facts_list, relations, diagnostics)
    return jsonify(db.get_knowledge()), 201


@app.post("/api/relations/<path:relation_id>/feedback")
def set_feedback(relation_id: str):
    payload = request.get_json(force=True, silent=True) or {}
    action = payload.get("action")
    override_type = payload.get("override_type")

    if action not in {"approve", "reject", "override"}:
        return jsonify(error="Action must be 'approve', 'reject', or 'override'."), 400

    db.set_feedback(relation_id, action, override_type)
    return jsonify(db.get_knowledge()), 200


@app.post("/api/reset")
def reset():
    db.clear()
    return ("", 204)


if __name__ == "__main__":
    app.run(debug=True, port=8000)
