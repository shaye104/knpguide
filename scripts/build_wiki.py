#!/usr/bin/env python3
"""
Generate a small static "wiki" from PDFs in ./documents into ./public/wiki.

This runs locally (not in Cloudflare). The generated HTML is committed to the repo
so Cloudflare Pages can serve it as a static site.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pdfminer.high_level import extract_text  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "documents"
OUT_DIR = ROOT / "public" / "wiki"
OUT_DOCS_DIR = OUT_DIR / "docs"


def slugify(name: str) -> str:
  s = name.lower().strip()
  s = re.sub(r"\.pdf$", "", s, flags=re.I)
  s = re.sub(r"[^a-z0-9]+", "-", s)
  s = re.sub(r"-{2,}", "-", s).strip("-")
  return s or "doc"


def fmt_iso(dt: datetime) -> str:
  return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def escape(s: str) -> str:
  return html.escape(s, quote=True)


def read_pdf_text(path: Path) -> str:
  # pdfminer returns text with form-feed "\f" for page breaks.
  return extract_text(str(path)) or ""


def split_paragraphs(text: str) -> list[str]:
  # Normalize newlines, keep page breaks as standalone tokens.
  t = text.replace("\r\n", "\n").replace("\r", "\n")
  # Convert page breaks into blank-line separated blocks.
  t = t.replace("\f", "\n\n[[PAGEBREAK]]\n\n")
  blocks = [b.strip() for b in re.split(r"\n{2,}", t) if b.strip()]
  return blocks


def looks_like_heading(block: str) -> bool:
  one = " ".join(block.split())
  if len(one) > 80:
    return False
  if one.isupper():
    return True
  # Title-ish: no sentence punctuation and short.
  if re.search(r"[.!?]$", one):
    return False
  # Many words start capitalized.
  words = one.split()
  if len(words) >= 2 and sum(1 for w in words if w[:1].isupper()) >= max(2, len(words) - 1):
    return True
  return False


def build_doc_html(title: str, blocks: list[str], nav_html: str, updated_iso: str) -> str:
  parts: list[str] = []
  parts.append("<!doctype html>")
  parts.append('<html lang="en">')
  parts.append("<head>")
  parts.append('<meta charset="utf-8" />')
  parts.append('<meta name="viewport" content="width=device-width, initial-scale=1" />')
  parts.append(f"<title>{escape(title)} | KNP Guide</title>")
  parts.append('<link rel="stylesheet" href="/wiki/wiki.css" />')
  parts.append('<script src="/wiki/wiki.js" defer></script>')
  parts.append("</head>")
  parts.append("<body>")
  parts.append(
    """
    <header class="topbar">
      <div class="wrap topbar-inner">
        <a class="brand" href="/wiki/">KNP Guide</a>
        <nav class="topnav">
          <a href="/wiki/">Wiki</a>
          <a href="/wiki/arrests-fines.html">Arrests &amp; Fines</a>
          <a href="/">Home</a>
        </nav>
      </div>
    </header>
    """.strip()
  )
  parts.append('<main class="wrap layout">')
  parts.append(f'<aside class="sidebar">{nav_html}</aside>')
  parts.append('<article class="content card">')
  parts.append(f"<h1>{escape(title)}</h1>")
  parts.append(f'<div class="meta">Updated: <time datetime="{updated_iso}">{escape(updated_iso)}</time></div>')

  for b in blocks:
    if b == "[[PAGEBREAK]]":
      parts.append('<hr class="pagebreak" />')
      continue
    if looks_like_heading(b):
      hid = slugify(b)[:64]
      parts.append(f'<h2 id="{escape(hid)}">{escape(" ".join(b.split()))}</h2>')
      continue
    # Preserve line breaks inside blocks where pdfminer keeps table-like layouts.
    if "\n" in b and len(b.splitlines()) >= 3:
      parts.append('<pre class="preblock">' + escape(b) + "</pre>")
    else:
      parts.append("<p>" + escape(" ".join(b.split())) + "</p>")

  parts.append("</article>")
  parts.append("</main>")
  parts.append(
    """
    <footer class="footer">
      <div class="wrap footer-inner">
        <div class="muted">KNP Guide</div>
        <div class="muted">Generated from source PDFs.</div>
      </div>
    </footer>
    """.strip()
  )
  parts.append("</body></html>")
  return "\n".join(parts) + "\n"


def build_nav(docs: list[tuple[str, str]]) -> str:
  # docs: (title, href)
  items = []
  items.append('<div class="nav-title">Documents</div>')
  items.append('<ul class="nav-list">')
  for title, href in docs:
    items.append(f'<li><a href="{escape(href)}">{escape(title)}</a></li>')
  items.append("</ul>")
  return "\n".join(items)


_TIME_RE = re.compile(r"(?i)^(\\d+\\s*(seconds?|minutes?))$")
_AMOUNT_RE = re.compile(r"[€ƒ$]\\s*\\d|\\d+\\s*(?:eur|euro|usd|gbp)|\\d+[\\.,]\\d+")


def parse_law_enforcement_guide(text: str) -> dict:
  lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
  # Remove empty runs, but keep section boundaries.
  def nonempty(seq):
    return [x for x in seq if x]

  # Locate "Arrest Reasons" block
  arrest_idx = None
  fine_idx = None
  for i, ln in enumerate(lines):
    if ln.lower() == "arrest reasons":
      arrest_idx = i
    if ln.lower() == "monetary fines":
      fine_idx = i
      break

  arrests: list[tuple[str, str]] = []
  fines: list[tuple[str, str]] = []
  notes: list[str] = []

  if arrest_idx is not None:
    chunk = lines[arrest_idx : (fine_idx or len(lines))]
    chunk = nonempty(chunk)
    # Skip headers
    # Expected pattern: "Arrest Reasons", "Arrest Reason", "Jail Time", then alternating reason/time
    # Some reasons wrap to multiple lines; we join until we see a time-like cell.
    buf: list[str] = []
    started = False
    for ln in chunk:
      if ln.lower() in ("arrest reasons", "arrest reason", "jail time", "jail time", "jail time "):
        started = True
        continue
      if not started:
        continue
      if _TIME_RE.match(ln):
        reason = " ".join(buf).strip()
        if reason:
          arrests.append((reason, ln))
        buf = []
        continue
      buf.append(ln)

  if fine_idx is not None:
    chunk = lines[fine_idx:]
    chunk = nonempty(chunk)
    # Skip "Monetary Fines", "Monetary Fine", "Amount"
    buf: list[str] = []
    started = False
    for ln in chunk:
      lower = ln.lower()
      if lower in ("monetary fines", "monetary fine", "amount"):
        started = True
        continue
      if not started:
        continue
      if ln.startswith("*"):
        notes.append(ln)
        continue
      if _AMOUNT_RE.search(ln) and buf:
        fine_name = " ".join(buf).strip()
        if fine_name:
          fines.append((fine_name, ln))
        buf = []
        continue
      buf.append(ln)

  return {"arrests": arrests, "fines": fines, "notes": notes}


def build_arrests_fines_page(parsed: dict, nav_html: str, updated_iso: str) -> str:
  arrests = parsed.get("arrests") or []
  fines = parsed.get("fines") or []
  notes = parsed.get("notes") or []

  parts: list[str] = []
  parts.append("<!doctype html>")
  parts.append('<html lang="en">')
  parts.append("<head>")
  parts.append('<meta charset="utf-8" />')
  parts.append('<meta name="viewport" content="width=device-width, initial-scale=1" />')
  parts.append("<title>Arrests & Fines | KNP Guide</title>")
  parts.append('<link rel="stylesheet" href="/wiki/wiki.css" />')
  parts.append('<script src="/wiki/wiki.js" defer></script>')
  parts.append("</head>")
  parts.append("<body>")
  parts.append(
    """
    <header class="topbar">
      <div class="wrap topbar-inner">
        <a class="brand" href="/wiki/">KNP Guide</a>
        <nav class="topnav">
          <a href="/wiki/">Wiki</a>
          <a class="active" href="/wiki/arrests-fines.html">Arrests &amp; Fines</a>
          <a href="/">Home</a>
        </nav>
      </div>
    </header>
    """.strip()
  )
  parts.append('<main class="wrap layout">')
  parts.append(f'<aside class="sidebar">{nav_html}</aside>')
  parts.append('<article class="content card">')
  parts.append("<h1>Arrests &amp; Fines</h1>")
  parts.append('<p class="muted">Source: Law Enforcement Guide.pdf</p>')
  parts.append(f'<div class="meta">Updated: <time datetime="{updated_iso}">{escape(updated_iso)}</time></div>')

  parts.append("<h2>Arrest reasons</h2>")
  parts.append('<div class="tablewrap"><table><thead><tr><th>Reason</th><th>Jail time</th></tr></thead><tbody>')
  for reason, jail in arrests:
    parts.append(f"<tr><td>{escape(reason)}</td><td>{escape(jail)}</td></tr>")
  if not arrests:
    parts.append('<tr><td colspan="2" class="muted">No arrest data parsed.</td></tr>')
  parts.append("</tbody></table></div>")

  parts.append("<h2>Monetary fines</h2>")
  parts.append('<div class="tablewrap"><table><thead><tr><th>Fine</th><th>Amount</th></tr></thead><tbody>')
  for name, amount in fines:
    parts.append(f"<tr><td>{escape(name)}</td><td>{escape(amount)}</td></tr>")
  if not fines:
    parts.append('<tr><td colspan="2" class="muted">No fine data parsed.</td></tr>')
  parts.append("</tbody></table></div>")

  if notes:
    parts.append("<h2>Notes</h2>")
    for n in notes:
      parts.append("<p>" + escape(n) + "</p>")

  parts.append(
    '<p class="muted">For full wording and context, open <a href="/wiki/docs/law-enforcement-guide.html">Law Enforcement Guide</a>.</p>'
  )
  parts.append("</article></main>")
  parts.append(
    """
    <footer class="footer">
      <div class="wrap footer-inner">
        <div class="muted">KNP Guide</div>
        <div class="muted">Generated from source PDFs.</div>
      </div>
    </footer>
    """.strip()
  )
  parts.append("</body></html>")
  return "\n".join(parts) + "\n"


def build_index_html(docs: list[dict], updated_iso: str) -> str:
  cards = []
  for d in docs:
    cards.append(
      f"""
      <a class="doccard" href="{escape(d['href'])}">
        <div class="doccard-title">{escape(d['title'])}</div>
        <div class="doccard-meta">{escape(d['pages'])} pages</div>
      </a>
      """.strip()
    )

  parts: list[str] = []
  parts.append("<!doctype html>")
  parts.append('<html lang="en">')
  parts.append("<head>")
  parts.append('<meta charset="utf-8" />')
  parts.append('<meta name="viewport" content="width=device-width, initial-scale=1" />')
  parts.append("<title>Wiki | KNP Guide</title>")
  parts.append('<link rel="stylesheet" href="/wiki/wiki.css" />')
  parts.append('<script src="/wiki/wiki.js" defer></script>')
  parts.append("</head>")
  parts.append("<body>")
  parts.append(
    """
    <header class="topbar">
      <div class="wrap topbar-inner">
        <a class="brand" href="/wiki/">KNP Guide</a>
        <nav class="topnav">
          <a class="active" href="/wiki/">Wiki</a>
          <a href="/wiki/arrests-fines.html">Arrests &amp; Fines</a>
          <a href="/">Home</a>
        </nav>
      </div>
    </header>
    """.strip()
  )
  parts.append('<main class="wrap">')
  parts.append('<section class="hero card">')
  parts.append("<h1>Knowledge Base</h1>")
  parts.append("<p class=\"muted\">Search and browse the official KNP/NLD documents.</p>")
  parts.append('<div class="searchbar"><input id="wiki-search" type="search" placeholder="Search documents..." /></div>')
  parts.append(f'<div class="meta">Updated: <time datetime="{updated_iso}">{escape(updated_iso)}</time></div>')
  parts.append("</section>")
  parts.append('<section id="wiki-results" class="docgrid">')
  parts.append("\n".join(cards))
  parts.append("</section>")
  parts.append("</main>")
  parts.append(
    """
    <footer class="footer">
      <div class="wrap footer-inner">
        <div class="muted">KNP Guide</div>
        <div class="muted">Search runs locally in your browser.</div>
      </div>
    </footer>
    """.strip()
  )
  parts.append("</body></html>")
  return "\n".join(parts) + "\n"


def write_file(path: Path, content: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content, encoding="utf-8")


def main() -> int:
  if not DOCS_DIR.exists():
    raise SystemExit(f"Missing documents dir: {DOCS_DIR}")

  now = datetime.now(timezone.utc)
  updated_iso = fmt_iso(now)

  if OUT_DIR.exists():
    shutil.rmtree(OUT_DIR)
  OUT_DOCS_DIR.mkdir(parents=True, exist_ok=True)

  # Static assets
  write_file(OUT_DIR / "wiki.css", (ROOT / "scripts" / "wiki.css").read_text(encoding="utf-8"))
  write_file(OUT_DIR / "wiki.js", (ROOT / "scripts" / "wiki.js").read_text(encoding="utf-8"))

  docs_meta: list[dict] = []
  nav_items: list[tuple[str, str]] = []

  pdfs = sorted([p for p in DOCS_DIR.glob("*.pdf") if p.is_file()], key=lambda p: p.name.lower())
  for pdf in pdfs:
    title = pdf.stem
    slug = slugify(pdf.name)
    href = f"/wiki/docs/{slug}.html"
    text = read_pdf_text(pdf)
    blocks = split_paragraphs(text)
    # Estimate pages via form-feed marker count (pdfminer uses one per page break).
    pages = max(1, text.count("\f") + 1)

    nav_items.append((title, href))
    docs_meta.append({"title": title, "href": href, "slug": slug, "pages": str(pages), "text": " ".join(text.split())})

  nav_html = build_nav(nav_items)

  # Write doc pages
  for d in docs_meta:
    pdf = DOCS_DIR / f"{d['title']}.pdf"
    # When the title contains '.' etc, use original by slug lookup instead.
    pdf = next((p for p in pdfs if slugify(p.name) == d["slug"]), None)
    if not pdf:
      continue
    text = read_pdf_text(pdf)
    blocks = split_paragraphs(text)
    doc_html = build_doc_html(d["title"], blocks, nav_html, updated_iso)
    write_file(OUT_DOCS_DIR / f"{d['slug']}.html", doc_html)

  # Arrests/Fines special page
  leg = next((p for p in pdfs if p.name.lower() == "law enforcement guide.pdf".lower()), None)
  if leg:
    parsed = parse_law_enforcement_guide(read_pdf_text(leg))
    write_file(OUT_DIR / "arrests-fines.html", build_arrests_fines_page(parsed, nav_html, updated_iso))

  # Search index
  index = [{"title": d["title"], "href": d["href"], "text": d["text"][:20000]} for d in docs_meta]
  write_file(OUT_DIR / "search-index.json", json.dumps({"updated_at": updated_iso, "docs": index}, indent=2))

  # Index page
  write_file(OUT_DIR / "index.html", build_index_html(docs_meta, updated_iso))

  return 0


if __name__ == "__main__":
  raise SystemExit(main())

