# BBultra-KBGen — Blackboard Ultra → Knowledge Base Generator

Converts any Blackboard Ultra course export (ZIP) into two independent outputs:

- **`bb_documents.json`** — structured records ready to load into Microsoft Dataverse via Power Automate, for use as a Copilot Studio knowledge base
- **`outputs/site/`** — a fully self-contained offline website with all images, navigation, and links intact

Compatible with any standard Blackboard Ultra course export. The scripts auto-detect the ZIP, manifest structure, BB server hostname, and embedded asset paths — no configuration needed.

---

## Requirements

- Python 3.10 or later
- `beautifulsoup4 >= 4.12` and `lxml >= 5.0` (step 2 only; step 3 uses stdlib)

```bat
python --version
```

---

## Setup

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Place your Blackboard Ultra export ZIP in `src/`. The scripts auto-detect any `.zip` there.

---

## Running the Pipeline

```bat
python step1_inspect_zip.py   # inspect ZIP → outputs/bb_inventory.json
python step2_extract.py       # extract content → outputs/bb_documents.json
python step3_static_site.py   # build offline site → outputs/site/index.html
```

**Steps 2 and 3 are independent** — both read `bb_inventory.json` from step 1, but neither requires the other. Run either or both depending on your use case.

---

## What Each Step Does

### Step 1 — Inspect
Scans the ZIP index without extracting anything. Prints a file-type summary and saves `outputs/bb_inventory.json` for downstream steps.

### Step 2 — Extract & Clean (Dataverse JSON)
Reads each `.dat` content file, strips Blackboard chrome, inlines embedded HTML components, converts file-attachment image anchors to `<img>` tags, and outputs:
- `outputs/bb_documents.json` — one record per content page, ready for Dataverse import
- `outputs/bb_extracted/*.txt` — plain-text version of each page

### Step 3 — Static Site Mirror
Builds a self-contained offline copy of the course:
- All HTML pages cleaned and re-skinned with a minimal stylesheet
- Inline images resolved from the ZIP and placed relative to each page
- File-attachment images displayed inline (converted from BB's `<a data-bbfile>` format)
- Embedded components (Vimeo, Padlet, etc.) preserved as `<div class="bb-embedded">`
- Internal navigation sidebar reflecting the original course structure
- External links marked with ↗ and opening in a new tab
- Images not included in the export show a `[image not in export]` placeholder

---

## Folder Structure

```
BBultra-KBGen/
  src/                          ← place your Blackboard Ultra ZIP here (gitignored)
  outputs/                      ← all generated files (gitignored)
    bb_inventory.json           ← step 1 output
    bb_extracted/               ← plain .txt per page (step 2)
    bb_documents.json           ← JSON for Dataverse import (step 2)
    site/                       ← offline website (step 3)
      index.html
      assets/style.css
      <ref>.html                ← one page per content item
      csfiles/                  ← extracted course images
  step1_inspect_zip.py
  step2_extract.py
  step3_static_site.py
  requirements.txt
  CLAUDE.md
```

---

## Output: bb_documents.json (step 2)

```json
{
  "id":          "a3f9c1d02b4e",
  "source_path": "res00274.dat",
  "module":      "Week 3 — Content Design",
  "title":       "Adding and Creating Content",
  "text":        "Full cleaned body text...",
  "char_count":  4821,
  "links": [
    { "type": "hyperlink", "url": "https://help.blackboard.com/...", "text": "BB Help" },
    { "type": "image",     "url": "https://your-bb-server/bbcswebdav/xid-NNN_1", "text": "diagram.png" },
    { "type": "embed",     "url": "https://player.vimeo.com/video/...", "text": "" }
  ]
}
```

- `id` — stable 12-char MD5 of `source_path` (use as Dataverse alternate key)
- `links[].url` — asset links use the real Blackboard server URL (auto-detected from the ZIP)
- `module` — depth-2 ancestor in the course manifest (top-level course section)

---

## Loading into Dataverse (after step 2)

### Option A — Power Automate Dataflow (recommended)
1. Upload `outputs/bb_documents.json` to OneDrive or SharePoint
2. `make.powerapps.com` → **Dataflows → New Dataflow**
3. Source: **JSON** → point at the file
4. Map columns in Power Query and run

### Option B — Power Automate Flow
1. Upload JSON to SharePoint
2. Flow: **Get file content → Parse JSON → Apply to each → Dataverse: Create or update row** (upsert on `bb_contentid`)

### Dataverse Table Setup

| Display Name | Logical Name     | Type           | Notes                    |
|--------------|------------------|----------------|--------------------------|
| Content ID   | bb_contentid     | Text (50)      | Set as **Alternate Key** |
| Title        | bb_title         | Text (500)     |                          |
| Module       | bb_module        | Text (255)     |                          |
| Source Path  | bb_sourcepath    | Text (1000)    |                          |
| Body Text    | bb_bodytext      | Multiline Text | Up to 1,048,576 chars    |
| Links JSON   | bb_links         | Multiline Text | JSON array               |
| Char Count   | bb_charcount     | Whole Number   |                          |

---

## Connecting to Copilot Studio

1. Copilot Studio → **Knowledge → Add Knowledge → Dataverse**
2. Select `bb_coursecontents`
3. Map `bb_title` → Title, `bb_bodytext` → Content

---

## Compatibility Notes

The scripts are designed for standard Blackboard Ultra exports and handle:
- IMS manifest formats (`imsmanifest.xml`, `bb_manifest.xml`)
- BB content type `resource/x-bb-document` (the standard type for all Ultra document pages)
- BB `@X@` URL tokens for embedded files and assets
- BB `data-bbfile` JSON anchors (inline images stored as file attachments)
- csfiles HTML embedded components (rich tiles, Vimeo embeds, interactive widgets)
- The BB server hostname is auto-detected from csfiles HTML — no hardcoded URLs

The module hierarchy uses depth-2 of the normalised manifest tree, which is the standard top-level section depth in all BB Ultra exports.

---

## Deactivating the Virtual Environment

```bat
deactivate
```
