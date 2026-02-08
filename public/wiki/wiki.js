/* Client-side search for /wiki (no backend). */

async function loadIndex() {
  const res = await fetch('/wiki/search-index.json', { cache: 'no-store' });
  if (!res.ok) return null;
  return res.json();
}

function normalize(s) {
  return String(s || '').toLowerCase();
}

function clamp(str, max) {
  const s = String(str || '');
  if (s.length <= max) return s;
  return s.slice(0, Math.max(0, max - 3)).trimEnd() + '...';
}

function renderCards(container, docs) {
  container.innerHTML = '';
  docs.forEach((d) => {
    const a = document.createElement('a');
    a.className = 'doccard';
    a.href = d.href;
    const t = document.createElement('div');
    t.className = 'doccard-title';
    t.textContent = d.title;
    const m = document.createElement('div');
    m.className = 'doccard-meta';
    m.textContent = clamp(d.snippet || '', 140);
    a.appendChild(t);
    a.appendChild(m);
    container.appendChild(a);
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  const input = document.getElementById('wiki-search');
  const results = document.getElementById('wiki-results');
  if (!input || !results) return;

  const index = await loadIndex();
  if (!index || !Array.isArray(index.docs)) return;

  // Initial render uses title-only cards from the existing HTML.
  const docs = index.docs.map((d) => ({ title: d.title, href: d.href, text: d.text || '' }));

  const run = () => {
    const q = normalize(input.value).trim();
    if (!q) {
      // Restore default view: title-only cards (no snippet).
      renderCards(results, docs.map((d) => ({ title: d.title, href: d.href, snippet: '' })));
      return;
    }
    const tokens = q.split(/\s+/).filter(Boolean);
    const scored = [];
    for (const d of docs) {
      const hay = normalize(d.title + ' ' + d.text);
      let score = 0;
      for (const tok of tokens) {
        const idx = hay.indexOf(tok);
        if (idx === -1) {
          score = -1;
          break;
        }
        score += (idx < 80 ? 5 : 1);
      }
      if (score < 0) continue;

      const first = tokens.length ? hay.indexOf(tokens[0]) : 0;
      const start = Math.max(0, (first === -1 ? 0 : first) - 60);
      const snippet = clamp(d.text.slice(start, start + 220).replace(/\s+/g, ' ').trim(), 180);
      scored.push({ title: d.title, href: d.href, snippet, score });
    }
    scored.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
    renderCards(results, scored.slice(0, 60));
  };

  input.addEventListener('input', run);
  run();
});

