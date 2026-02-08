# politie.evodev.uk / KNP Guide

Static site served via Cloudflare Pages.

## Local structure

- `public/` is the Cloudflare Pages output directory.
- Entry point: `public/index.html`
- Source PDFs: `documents/*.pdf`
- Generated wiki pages: `public/wiki/*`

## Generate wiki from PDFs (local)

This runs locally and writes static HTML into `public/wiki/` so Pages can serve it.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip pdfminer.six
python scripts/build_wiki.py
```

## Cloudflare Pages settings (GitHub deploy)

- Framework preset: `None`
- Build command: *(empty)*
- Output directory: `public`
