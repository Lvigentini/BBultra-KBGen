# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A three-step pipeline to convert a Blackboard Ultra course export (ZIP) into:
- **Step 2**: A structured `bb_documents.json` for import into Microsoft Dataverse (via Power Automate)
- **Step 3**: A self-contained offline static website

The downstream use case is a **Copilot Studio knowledge base** fed from Dataverse.

## Setup

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Place the Blackboard Ultra ZIP in `src/`. Scripts auto-detect any `.zip` there — no config needed.

Dependencies: `beautifulsoup4>=4.12`, `lxml>=5.0` (step 2 only; step 3 uses stdlib).

## Running the Pipeline

```bat
python step1_inspect_zip.py   # inspect ZIP structure → outputs/bb_inventory.json
python step2_extract.py       # clean HTML → outputs/bb_documents.json + bb_extracted/
python step3_static_site.py   # static site → outputs/site/index.html
```

Steps 2 and 3 are **independent** — both read `bb_inventory.json` from step 1, but neither requires the other.

## Architecture

### Data flow

```
src/*.zip
  │
  ├─ step1 → outputs/bb_inventory.json
  │             (html_files list, asset_files list, ext_summary)
  │
  ├─ step2 → outputs/bb_documents.json   (array of cleaned page records)
  │          outputs/bb_extracted/*.txt  (plain-text per page)
  │
  └─ step3 → outputs/site/              (self-contained offline website)
               index.html
               assets/style.css
               <module>/<page>.html
               <module>/images/
```

### Key design decisions

**step2** uses `BeautifulSoup` + `lxml`. It strips BB chrome via CSS selectors (`BB_CHROME_SELECTORS`), extracts hyperlinks/images/embeds into a `links` array, and skips pages with fewer than 50 characters of body text. IDs are stable 12-char MD5 hashes of the source path.

**step3** uses only the Python stdlib (`HTMLParser`, `zipfile`, `shutil`). The `LinkRewriter` class is a single-pass `HTMLParser` subclass that simultaneously: removes BB chrome elements (by id/class fragments in `BB_CHROME_IDS_CLASSES`), rewrites relative and absolute BB asset URLs to relative paths, marks external links with `.ext-link` CSS class, and collects asset paths for extraction. Images are resolved in three ways: relative path resolution, filename-only lookup in `asset_by_name` dict (for absolute BB URLs), and a fallback to original URL for unresolvable externals.

The static site mirrors the ZIP's folder structure exactly (`site_path = html_zip_path`). CSS depth is calculated from path depth to correctly relativise the `assets/style.css` link.

### Output schema (`bb_documents.json`)

```json
{
  "id":          "a3f9c1d02b4e",    // MD5[:12] of source_path
  "source_path": "module02/week3/reading.html",
  "module":      "module02",
  "title":       "Week 3 Required Reading",
  "text":        "...",
  "char_count":  4821,
  "links": [
    { "type": "hyperlink"|"image"|"embed", "url": "...", "text": "..." }
  ]
}
```

### Dataverse table

Target table `bb_coursecontents` with `bb_contentid` as Alternate Key. Loaded via Power Automate Dataflow (recommended) or Flow. Connected to Copilot Studio via Knowledge → Dataverse, mapping `bb_title` → Title, `bb_text` → Content.
