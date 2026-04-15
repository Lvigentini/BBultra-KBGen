"""
STEP 2 - Blackboard Ultra .dat Extractor & Cleaner
Reads course content from .dat resource files (the real pages), strips
Blackboard chrome, appends text from embedded HTML components, and outputs
clean JSON ready to load into Dataverse via Power Automate.

Usage:
    python step2_extract.py

Input:
    src/                           <- Blackboard Ultra ZIP (auto-detected)
    outputs/bb_inventory.json      <- produced by step1_inspect_zip.py

Output:
    outputs/bb_extracted/          <- plain .txt files, one per page
    outputs/bb_documents.json      <- structured records for Dataverse import
"""

import sys, zipfile, json, re, hashlib
import html as html_lib
import xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── PATHS ─────────────────────────────────────────────────────────────────────
OUT_DIR        = Path("outputs")
INVENTORY_FILE = OUT_DIR / "bb_inventory.json"
EXTRACTED_DIR  = OUT_DIR / "bb_extracted"
DOCS_JSON      = OUT_DIR / "bb_documents.json"

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Only these BB content types produce readable pages
DOC_TYPES = {"resource/x-bb-document"}

# BB chrome selectors to strip from page HTML
BB_CHROME_SELECTORS = [
    "nav", "header", "footer", "script", "style", "noscript",
    ".bb-learn-navigation", ".globalNavigation",
    "#globalNavPageNavArea", "#breadcrumbs",
    ".navPanelSet", ".courseMenuPad",
]

# ── HELPERS ───────────────────────────────────────────────────────────────────

def make_id(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:12]


def preprocess_bb_urls(html_str: str, bb_server: str = "https://bb.local") -> str:
    """
    Replace BB @X@ URL tokens so downstream code can handle them normally.
    - unsafeHtml refs → marked with a recognisable scheme for text extraction
    - requestUrlStub bbcswebdav refs → real server URL (or bb.local fallback)
    - all other @X@ refs → dead link
    """
    # Embedded HTML component anchor: keep for text extraction below
    html_str = re.sub(
        r'@X@EmbeddedFile\.unsafeHtml@X@(bbcswebdav/[^"\'<>\s]+)',
        r'unsafe-html://\1',
        html_str,
    )
    # File/asset refs (PDFs, images, etc.) — use real server URL when known
    html_str = re.sub(
        r'@X@EmbeddedFile\.requestUrlStub@X@(bbcswebdav/[^"\'<>\s]+)',
        rf'{bb_server}/\1',
        html_str,
    )
    # Any remaining @X@ pattern (course-internal links, not offline-accessible)
    html_str = re.sub(r'@X@[^"\'<>\s]+', '#', html_str)
    return html_str


# ── MANIFEST PARSING ──────────────────────────────────────────────────────────

def parse_manifest_hierarchy(xml_bytes: bytes) -> list[dict]:
    """
    Parse BB Ultra imsmanifest.xml into an ordered TOC list.

    Each item has:
      title          - manifest title
      ref            - identifierref value (e.g. "res00274")
      dat_file       - corresponding .dat filename (e.g. "res00274.dat")
      type           - BB resource type
      depth          - nesting depth (normalised so shallowest = 0)
      module         - depth-2 ancestor title (top-level course section)
      display_title  - nav title; for "ultraDocumentBody" items uses parent title
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    def strip_ns(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    # Build resource type map: identifier → type
    res_types: dict[str, str] = {}
    for el in root.iter():
        if strip_ns(el.tag) == "resource":
            ident = el.attrib.get("identifier", "")
            rtype = el.attrib.get("type", "")
            if ident:
                res_types[ident] = rtype

    items: list[dict] = []

    def walk(node, depth=0):
        if strip_ns(node.tag) == "item":
            title_el = next((c for c in node if strip_ns(c.tag) == "title"), None)
            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            ref = node.attrib.get("identifierref", "")
            if title and ref:
                items.append({
                    "title":    title,
                    "ref":      ref,
                    "dat_file": ref + ".dat",
                    "type":     res_types.get(ref, ""),
                    "depth":    depth,
                })
        for child in node:
            walk(child, depth + 1)

    walk(root)
    if not items:
        return items

    # Normalise depths
    min_depth = min(i["depth"] for i in items)
    for i in items:
        i["depth"] -= min_depth

    # Assign module and display_title with a depth-tracking stack
    depth_titles: dict[int, str] = {}
    for item in items:
        d = item["depth"]
        title = item["title"]
        depth_titles[d] = title
        for k in list(depth_titles):
            if k > d:
                del depth_titles[k]

        # Module: depth-2 section name (skip generic root names)
        module = depth_titles.get(2) or depth_titles.get(1) or "General"
        if module.lower() in ("root", "--top--"):
            module = depth_titles.get(3) or "General"
        item["module"] = module

        # Display title: if "ultraDocumentBody", use nearest named parent
        if title.lower() in ("ultradocumentbody", ""):
            parent = depth_titles.get(d - 1, title)
            if parent.lower() in ("ultradocumentbody", "root", "--top--"):
                parent = depth_titles.get(d - 2, title)
            item["display_title"] = parent
        else:
            item["display_title"] = title

    return items


def _convert_bbfile_images(raw_html: str) -> str:
    """
    Convert <a data-bbfile=...> image attachments to <img> tags.
    Must run on the raw (entity-encoded) HTML before html.unescape().
    """
    def replacer(m):
        full = m.group(0)
        if "&quot;mimeType&quot;:&quot;image/" not in full:
            return full
        href_m = re.search(r'\bhref="([^"]+)"', full)
        if not href_m:
            return full
        href = href_m.group(1)
        alt_m = re.search(r'&quot;alternativeText&quot;:&quot;([^&]+)&quot;', full)
        alt = alt_m.group(1) if alt_m else ""
        return f'<img src="{href}" alt="{alt}">'

    return re.sub(
        r'<a\b[^>]*\bdata-bbfile\b[^>]*>[\s\S]*?</a>',
        replacer,
        raw_html,
        flags=re.I,
    )


# ── .dat FILE PARSING ─────────────────────────────────────────────────────────

def parse_dat_bytes(dat_bytes: bytes, bb_server: str = "https://bb.local") -> tuple[str, str, str]:
    """
    Parse a BB .dat XML file.
    Returns (title, content_handler_type, unescaped_html).
    html is empty string if the item has no body content.
    """
    try:
        root = ET.fromstring(dat_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return "", "", ""

    title_el   = root.find("TITLE")
    title      = title_el.get("value", "").strip() if title_el is not None else ""
    handler_el = root.find("CONTENTHANDLER")
    handler    = handler_el.get("value", "") if handler_el is not None else ""
    body_el    = root.find("BODY")
    text_el    = body_el.find("TEXT") if body_el is not None else None
    raw_html   = text_el.text if text_el is not None and text_el.text else ""
    if raw_html:
        raw_html = _convert_bbfile_images(raw_html)
    html_str   = preprocess_bb_urls(html_lib.unescape(raw_html), bb_server) if raw_html else ""

    return title, handler, html_str


# ── TEXT EXTRACTION ───────────────────────────────────────────────────────────

def _extract_embedded_text(soup, zf, csfiles_html_map: dict) -> str:
    """
    Find <a data-bbtype="embedded-unsafe-html"> elements, load the referenced
    csfiles HTML, and return the combined plain text of all embedded components.
    """
    texts = []
    for tag in soup.find_all("a", attrs={"data-bbtype": "embedded-unsafe-html"}):
        href = tag.get("href", "")
        xid_m = re.search(r"xid-(\d+)_\d+", href)
        if not xid_m or xid_m.group(1) not in csfiles_html_map:
            continue
        try:
            raw = zf.read(csfiles_html_map[xid_m.group(1)]).decode("utf-8", errors="replace")
            sub = BeautifulSoup(raw, "lxml")
            for el in sub.find_all(["script", "style"]):
                el.decompose()
            t = sub.get_text(separator="\n", strip=True)
            t = re.sub(r"\n{3,}", "\n\n", t).strip()
            if len(t) > 20:
                texts.append(t)
        except Exception:
            pass
    return "\n\n".join(texts)


def extract_links(soup) -> list:
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        # Strip trailing garbage from malformed BB HTML (missing closing quote)
        if " " in href:
            href = href.split()[0]
        if href.startswith(("javascript:", "#", "unsafe-html:")) or href in ("undefined", ""):
            continue
        links.append({"type": "hyperlink", "url": href, "text": tag.get_text(strip=True)[:200]})
    for tag in soup.find_all("img", src=True):
        src = tag["src"].strip()
        if " " in src:
            src = src.split()[0]
        if src and src not in ("undefined", ""):
            links.append({"type": "image", "url": src, "text": tag.get("alt", "")[:200]})
    for tag in soup.find_all(["iframe", "embed", "object"]):
        src = (tag.get("src") or tag.get("data") or "").strip()
        if src and not src.startswith(("#", "unsafe-html:")) and src != "undefined":
            links.append({"type": "embed", "url": src, "text": ""})
    return links


def html_to_doc(html_content: str, zf, csfiles_html_map: dict,
                source_id: str, title: str, module: str) -> dict | None:
    """Parse HTML, strip chrome, extract text + embedded component text + links."""
    try:
        soup = BeautifulSoup(html_content, "lxml")
    except Exception:
        return None

    for sel in BB_CHROME_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

    links = extract_links(soup)
    embedded_text = _extract_embedded_text(soup, zf, csfiles_html_map)

    body = soup.find("body") or soup
    main_text = body.get_text(separator="\n", strip=True)
    main_text = re.sub(r"\n{3,}", "\n\n", main_text).strip()

    full_text = (main_text + "\n\n" + embedded_text).strip() if embedded_text else main_text
    full_text = re.sub(r"\n{3,}", "\n\n", full_text).strip()

    if len(full_text) < 20:
        return None

    return {
        "id":          make_id(source_id),
        "source_path": source_id,
        "module":      module,
        "title":       title[:500],
        "text":        full_text,
        "char_count":  len(full_text),
        "links":       links,
    }


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run():
    if not INVENTORY_FILE.exists():
        print("❌  Run step1_inspect_zip.py first to generate outputs/bb_inventory.json")
        return

    with open(INVENTORY_FILE, encoding="utf-8") as f:
        inventory = json.load(f)

    zip_path = Path(inventory["zip_path"])
    print(f"\n📄  Extracting content from {zip_path.name} ...\n")

    # ── Collect manifest, csfiles HTML map, and real BB server URL ───────────────
    manifest_bytes = None
    csfiles_html_map: dict[str, str] = {}   # xid_num → zip path
    bb_server = "https://bb.local"           # overridden once we detect the hostname

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if Path(name).name.lower() in ("imsmanifest.xml", "bb_manifest.xml"):
                manifest_bytes = zf.read(name)
            if name.lower().endswith(".html") and "csfiles" in name.lower():
                m = re.search(r"xid-(\d+)_\d+\.html$", name, re.I)
                if m:
                    csfiles_html_map[m.group(1)] = name
                # Detect BB server hostname from the first csfiles HTML that has an absolute URL
                if bb_server == "https://bb.local" and len(csfiles_html_map) <= 5:
                    try:
                        sample = zf.read(name).decode("utf-8", errors="replace")
                        sm = re.search(r'(https?://[a-zA-Z0-9._:-]+)/bbcswebdav/', sample)
                        if sm:
                            bb_server = sm.group(1)
                    except Exception:
                        pass

    toc      = parse_manifest_hierarchy(manifest_bytes) if manifest_bytes else []
    doc_items = [i for i in toc if i["type"] in DOC_TYPES]

    print(f"   Manifest items : {len(toc)}")
    print(f"   Document items : {len(doc_items)}")
    print(f"   Embedded HTML  : {len(csfiles_html_map)}\n")

    # ── Extract documents ─────────────────────────────────────────────────────
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    documents = []
    skipped = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        available = set(zf.namelist())
        for item in doc_items:
            dat_file = item["dat_file"]
            if dat_file not in available:
                skipped += 1
                continue

            _, handler, html_content = parse_dat_bytes(zf.read(dat_file), bb_server)

            if handler not in DOC_TYPES or not html_content.strip():
                skipped += 1
                continue

            title = item["display_title"] or item["title"] or dat_file
            doc   = html_to_doc(html_content, zf, csfiles_html_map,
                                 source_id=dat_file,
                                 title=title,
                                 module=item["module"])
            if doc is None:
                skipped += 1
                continue

            safe_name = re.sub(r'[\\/:*?"<>|]', "_", dat_file)
            (EXTRACTED_DIR / (safe_name + ".txt")).write_text(doc["text"], encoding="utf-8")
            documents.append(doc)

    with open(DOCS_JSON, "w", encoding="utf-8") as f:
        json.dump(documents, f, indent=2, ensure_ascii=False)

    total_links = sum(len(d["links"]) for d in documents)
    print(f"✅  Done!")
    print(f"   Documents  : {len(documents)}")
    print(f"   Skipped    : {skipped}  (empty / non-content items)")
    print(f"   Links      : {total_links}  total across all pages")
    print(f"   Text files : {EXTRACTED_DIR.resolve()}")
    print(f"   JSON output: {DOCS_JSON.resolve()}")
    print()
    print("   Next: upload outputs/bb_documents.json to SharePoint/OneDrive,")
    print("         then use Power Automate Dataflow to load into Dataverse.\n")


if __name__ == "__main__":
    run()
