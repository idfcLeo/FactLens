const $ = s => document.querySelector(s);
let data = { relations: [], documents: [], facts: [], diagnostics: [] }, filter = 'all';

function escape(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
}

function evidence(f) {
  const ev = f.evidence;
  return `
    <div class="evidence">
      <div>
        <b>${escape(ev.document_name)}</b>
        <small>Page ${ev.page} · ${escape(f.kind)}${f.value ? ` · ${escape(f.value)}` : ''}${f.period ? ` · ${escape(f.period)}` : ''}</small>
        <p>“${escape(ev.excerpt)}”</p>
      </div>
      <button class="btn-preview" onclick="openPreview('${escape(ev.document_id)}', ${ev.page}, '${escape(ev.document_name)}')">🔍 Preview Page ${ev.page}</button>
    </div>
  `;
}

function render() {
  const rel = data.relations.filter(r => filter === 'all' || r.type === filter);

  $('#metrics').innerHTML = [
    ['Documents', data.documents.length],
    ['Candidate facts', data.facts.length],
    ['Relationships', data.relations.length],
    ['Watchlist', data.diagnostics.length]
  ].map(([l, n]) => `<div class="metric"><strong>${n}</strong><span>${l}</span></div>`).join('');

  $('#relations').innerHTML = rel.length ? rel.map(r => {
    const isApproved = r.user_action === 'approve';
    const isRejected = r.user_action === 'reject';
    const isOverridden = r.user_action === 'override';
    return `
      <article class="relation ${isRejected ? 'rejected' : ''} ${isApproved ? 'approved' : ''}" id="card-${escape(r.id)}">
        <div class="relation-head">
          <span class="badge ${r.type}">${r.type.toUpperCase()}</span>
          <span class="confidence">${Math.round(r.confidence * 100)}% signal</span>
        </div>
        <p class="reason">${escape(r.reason)}</p>
        <div class="evidence-grid">
          ${evidence(r.left)}
          ${evidence(r.right)}
        </div>
        <div class="feedback-actions">
          <button class="btn-approve ${isApproved ? 'active' : ''}" onclick="sendFeedback('${escape(r.id)}', 'approve')">${isApproved ? '✓ Approved' : '✓ Approve'}</button>
          <button class="btn-reject ${isRejected ? 'active' : ''}" onclick="sendFeedback('${escape(r.id)}', 'reject')">${isRejected ? '✗ Rejected' : '✗ Reject'}</button>
          <select class="feedback-select" onchange="sendFeedback('${escape(r.id)}', 'override', this.value)">
            <option value="">-- Override Classification --</option>
            <option value="corroborates" ${r.type === 'corroborates' ? 'selected' : ''}>Corroborates</option>
            <option value="contradicts" ${r.type === 'contradicts' ? 'selected' : ''}>Contradicts</option>
            <option value="reconciles" ${r.type === 'reconciles' ? 'selected' : ''}>Reconciles</option>
          </select>
          ${isApproved ? '<span class="review-tag tag-approved">✓ Approved by reviewer</span>' : ''}
          ${isRejected ? '<span class="review-tag tag-rejected">✗ Rejected by reviewer</span>' : ''}
          ${isOverridden ? `<span class="review-tag tag-overridden">✎ Overridden to ${escape(r.type.toUpperCase())} by reviewer</span>` : ''}
        </div>
      </article>
    `;
  }).join('') : '<div class="empty">No relationships in this view yet. Upload overlapping documents or choose another filter.</div>';

  $('#diagnostics').innerHTML = data.diagnostics.length ? data.diagnostics.map(d => `
    <div class="diagnostic">
      <b>${escape(d.type.replace('_', ' '))}</b> - ${escape(d.document)}<br>
      ${escape(d.message)}
    </div>
  `).join('') : '<div class="empty">No extraction failures reported. This does not guarantee every fact was captured.</div>';
}

async function refresh() {
  data = await fetch('/api/knowledge').then(r => r.json());
  render();
}

async function sendFeedback(relationId, action, overrideType = null) {
  $('#status').textContent = 'Saving feedback...';
  const res = await fetch(`/api/relations/${encodeURIComponent(relationId)}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, override_type: overrideType })
  });
  if (res.ok) {
    data = await res.json();
    $('#status').textContent = 'Feedback saved.';
    render();
  } else {
    $('#status').textContent = 'Failed to save feedback.';
  }
}

function openPreview(docId, pageNum, docName) {
  const modal = $('#preview-modal');
  $('#modal-title').textContent = `${docName} - Page ${pageNum}`;
  $('#modal-img').src = `/api/documents/${docId}/pages/${pageNum}/preview`;
  modal.showModal();
}

$('#modal-close').onclick = () => {
  $('#preview-modal').close();
};

$('#upload').onclick = async () => {
  let files = $('#files').files;
  if (!files.length) return $('#status').textContent = 'Choose at least one PDF.';
  $('#status').textContent = 'Extracting facts, parsing tables, and calculating TF-IDF embeddings...';
  let form = new FormData();
  [...files].forEach(f => form.append('files', f));
  let r = await fetch('/api/documents', { method: 'POST', body: form });
  let out = await r.json();
  if (!r.ok) {
    $('#status').textContent = out.error;
    return;
  }
  data = out;
  $('#status').textContent = `Analyzed ${files.length} document(s).`;
  render();
};

document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
  filter = b.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(x => x.classList.toggle('active', x === b));
  render();
});

$('#reset').onclick = async () => {
  await fetch('/api/reset', { method: 'POST' });
  await refresh();
  $('#status').textContent = 'Workspace reset.';
};

refresh();
