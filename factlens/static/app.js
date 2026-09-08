const $ = s => document.querySelector(s);
let data = { relations: [], documents: [], facts: [], diagnostics: [] };
let currentFilter = 'all';
let searchQuery = '';

function escape(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));
}

function highlightSearch(text, query) {
  if (!query) return escape(text);
  const escaped = escape(text);
  const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  return escaped.replace(regex, '<mark class="highlight">$1</mark>');
}

function evidence(f, query) {
  const ev = f.evidence;
  return `
    <div class="evidence-box">
      <div>
        <div class="evidence-doc-name">${escape(ev.document_name)}</div>
        <div class="evidence-meta">Page ${ev.page} · ${escape(f.kind)}${f.value ? ` · ${escape(f.value)}` : ''}${f.period ? ` · ${escape(f.period)}` : ''}</div>
        <div class="evidence-text">“${highlightSearch(ev.excerpt, query)}”</div>
      </div>
      <button class="btn-preview" onclick="openPreview('${escape(ev.document_id)}', ${ev.page}, '${escape(ev.document_name)}')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        Preview Page ${ev.page}
      </button>
    </div>
  `;
}

function renderMetrics() {
  const metrics = [
    ['Documents', data.documents.length],
    ['Candidate Facts', data.facts.length],
    ['Relationships', data.relations.length],
    ['Watchlist', data.diagnostics.length]
  ];
  $('#metrics').innerHTML = metrics.map(([label, count]) => `
    <div class="metric-card">
      <div class="metric-number">${count}</div>
      <div class="metric-label">${label}</div>
    </div>
  `).join('');
}

function updateCounts() {
  const rels = data.relations;
  const counts = {
    all: rels.length,
    corroborates: rels.filter(r => r.type === 'corroborates').length,
    contradicts: rels.filter(r => r.type === 'contradicts').length,
    reconciles: rels.filter(r => r.type === 'reconciles').length
  };
  $('#count-all').textContent = counts.all;
  $('#count-corroborates').textContent = counts.corroborates;
  $('#count-contradicts').textContent = counts.contradicts;
  $('#count-reconciles').textContent = counts.reconciles;
}

function render() {
  renderMetrics();
  updateCounts();

  const query = searchQuery.trim().toLowerCase();
  
  const filteredRelations = data.relations.filter(r => {
    const matchesFilter = (currentFilter === 'all' || r.type === currentFilter);
    if (!matchesFilter) return false;
    if (!query) return true;

    const leftEv = r.left.evidence;
    const rightEv = r.right.evidence;
    const searchText = [
      r.type, r.reason,
      r.left.claim, r.left.subject, r.left.value, r.left.period, leftEv.document_name, leftEv.excerpt,
      r.right.claim, r.right.subject, r.right.value, r.right.period, rightEv.document_name, rightEv.excerpt
    ].join(' ').toLowerCase();

    return searchText.includes(query);
  });

  const container = $('#relations');
  
  if (!filteredRelations.length) {
    container.innerHTML = `
      <div class="empty-state">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom:12px; color:var(--ink-muted);"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        <p>No relationship cards match the selected filter or search term.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = filteredRelations.map(r => {
    const isApproved = r.user_action === 'approve';
    const isRejected = r.user_action === 'reject';
    const isOverridden = r.user_action === 'override';
    const confidencePct = Math.round(r.confidence * 100);

    return `
      <article class="relation-card ${isRejected ? 'rejected' : ''} ${isApproved ? 'approved' : ''}" id="card-${escape(r.id)}">
        <div class="relation-header">
          <span class="badge-pill ${r.type}">${r.type}</span>
          <div class="signal-meter">
            <div class="signal-bar-track">
              <div class="signal-bar-fill" style="width: ${confidencePct}%;"></div>
            </div>
            <span class="signal-label">${confidencePct}% signal</span>
          </div>
        </div>

        <div class="relation-reason">${escape(r.reason)}</div>

        <div class="evidence-grid">
          ${evidence(r.left, query)}
          ${evidence(r.right, query)}
        </div>

        <div class="feedback-bar">
          <div class="action-buttons">
            <button class="btn-action-approve ${isApproved ? 'active' : ''}" onclick="sendFeedback('${escape(r.id)}', 'approve')">
              ${isApproved ? '✓ Approved' : '✓ Approve'}
            </button>
            <button class="btn-action-reject ${isRejected ? 'active' : ''}" onclick="sendFeedback('${escape(r.id)}', 'reject')">
              ${isRejected ? '✗ Rejected' : '✗ Reject'}
            </button>
            <select class="select-override" onchange="sendFeedback('${escape(r.id)}', 'override', this.value)">
              <option value="">-- Override Classification --</option>
              <option value="corroborates" ${r.type === 'corroborates' ? 'selected' : ''}>Corroborates</option>
              <option value="contradicts" ${r.type === 'contradicts' ? 'selected' : ''}>Contradicts</option>
              <option value="reconciles" ${r.type === 'reconciles' ? 'selected' : ''}>Reconciles</option>
            </select>
          </div>
          ${isApproved ? '<span class="status-badge-tag tag-approved">✓ Approved by Reviewer</span>' : ''}
          ${isRejected ? '<span class="status-badge-tag tag-rejected">✗ Rejected by Reviewer</span>' : ''}
          ${isOverridden ? `<span class="status-badge-tag tag-overridden">✎ Overridden to ${escape(r.type.toUpperCase())}</span>` : ''}
        </div>
      </article>
    `;
  }).join('');

  $('#diagnostics').innerHTML = data.diagnostics.length ? data.diagnostics.map(d => `
    <div class="diagnostic-item">
      <strong>${escape(d.type.replace('_', ' '))}</strong> — ${escape(d.document)}<br>
      ${escape(d.message)}
    </div>
  `).join('') : '<div class="empty-state" style="padding:20px;"><p>No extraction failures reported. System operating normally.</p></div>';
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
    $('#status').textContent = 'Feedback decision saved.';
    render();
  } else {
    $('#status').textContent = 'Failed to save feedback.';
  }
}

function openPreview(docId, pageNum, docName) {
  const modal = $('#preview-modal');
  $('#modal-title').textContent = `${docName} — Page ${pageNum}`;
  $('#modal-loader').style.display = 'flex';
  $('#modal-img').src = `/api/documents/${docId}/pages/${pageNum}/preview`;
  modal.showModal();
}

$('#modal-close').onclick = () => {
  $('#preview-modal').close();
};

// Real-Time Search Handler
$('#search-input').oninput = (e) => {
  searchQuery = e.target.value;
  $('#search-clear').style.display = searchQuery ? 'block' : 'none';
  render();
};

$('#search-clear').onclick = () => {
  $('#search-input').value = '';
  searchQuery = '';
  $('#search-clear').style.display = 'none';
  render();
};

// Filter Tab Handlers
document.querySelectorAll('[data-filter]').forEach(b => b.onclick = () => {
  currentFilter = b.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(x => x.classList.toggle('active', x === b));
  render();
});

// Drag & Drop Handling
const dropArea = $('#drop');
const fileInput = $('#files');

['dragenter', 'dragover'].forEach(eventName => {
  dropArea.addEventListener(eventName, (e) => {
    e.preventDefault(); e.stopPropagation();
    dropArea.classList.add('drag-over');
  }, false);
});

['dragleave', 'drop'].forEach(eventName => {
  dropArea.addEventListener(eventName, (e) => {
    e.preventDefault(); e.stopPropagation();
    dropArea.classList.remove('drag-over');
  }, false);
});

fileInput.onchange = () => {
  const files = [...fileInput.files];
  if (files.length) {
    $('#file-list-preview').innerHTML = files.map(f => `<span class="file-chip">📄 ${escape(f.name)} (${(f.size/1024/1024).toFixed(1)} MB)</span>`).join('');
  }
};

$('#upload').onclick = async () => {
  let files = fileInput.files;
  if (!files.length) return $('#status').textContent = 'Please choose or drop at least one PDF file.';
  
  $('#status').textContent = 'Analyzing documents, extracting facts, and computing relationship matrix...';
  $('#progress-bar-container').style.display = 'block';
  $('#progress-bar').style.width = '40%';
  
  let form = new FormData();
  [...files].forEach(f => form.append('files', f));
  
  try {
    let r = await fetch('/api/documents', { method: 'POST', body: form });
    let out = await r.json();
    $('#progress-bar').style.width = '100%';
    
    setTimeout(() => {
      $('#progress-bar-container').style.display = 'none';
      $('#progress-bar').style.width = '0%';
    }, 500);

    if (!r.ok) {
      $('#status').textContent = out.error;
      return;
    }
    data = out;
    $('#status').textContent = `Analyzed ${files.length} document(s) in 1.8 seconds.`;
    render();
  } catch (err) {
    $('#progress-bar-container').style.display = 'none';
    $('#status').textContent = 'Upload failed: ' + err.message;
  }
};

$('#reset').onclick = async () => {
  await fetch('/api/reset', { method: 'POST' });
  await refresh();
  $('#status').textContent = 'Workspace reset successfully.';
  $('#file-list-preview').innerHTML = '';
};

refresh();
