"""
STEP 3 - Blackboard Ultra Static Site Builder
Extracts the entire course from the ZIP into a self-contained offline website.

Usage:
    python step3_static_site.py

Input:
    src/                           <- Blackboard Ultra ZIP (auto-detected)
    outputs/bb_inventory.json      <- produced by step1_inspect_zip.py

Output:
    outputs/site/                  <- open index.html in any browser
      index.html                   <- course map / top-level navigation
      assets/
        style.css                  <- minimal stylesheet (no BB dependency)
      <module>/
        <page>.html                <- cleaned content, links rewritten
        images/                    <- images referenced by that page
      images/                      <- central image pool (BB csfiles etc.)
"""

import sys, zipfile, json, re, shutil, os
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, unquote
from html.parser import HTMLParser
from collections import defaultdict

# Ensure emoji and Unicode print correctly on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── PATHS ─────────────────────────────────────────────────────────────────────
OUT_DIR        = Path("outputs")
SITE_DIR       = OUT_DIR / "site"
ASSETS_DIR     = SITE_DIR / "assets"
INVENTORY_FILE = OUT_DIR / "bb_inventory.json"

# ── ASSET EXTENSIONS ──────────────────────────────────────────────────────────
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".bmp", ".ico"}
MEDIA_EXTS = {".mp4", ".mp3", ".wav", ".ogg", ".webm"}
DOC_EXTS   = {".pdf", ".docx", ".pptx", ".xlsx"}
ASSET_EXTS = IMAGE_EXTS | MEDIA_EXTS | DOC_EXTS

# BB chrome selectors to remove
BB_CHROME_IDS_CLASSES = {
    "globalnavigation", "globalnavpage", "breadcrumb",
    "navpanelset", "coursemenupad", "bb-learn",
}

# ── MINIMAL CSS ───────────────────────────────────────────────────────────────
STYLESHEET = """\
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 16px;
    line-height: 1.7;
    color: #1a1a1a;
    background: #f5f6fa;
    display: grid;
    grid-template-columns: 260px 1fr;
    min-height: 100vh;
}

#sidebar {
    background: #1b2a4a;
    color: #c8d6f0;
    padding: 1.5rem 1rem;
    position: sticky;
    top: 0;
    height: 100vh;
    overflow-y: auto;
}
#sidebar h1 { font-size: 1rem; color: #fff; margin-bottom: 1.2rem; line-height: 1.3; }
#sidebar .module-title {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: #7a9bc4;
    margin: 1.2rem 0 0.3rem;
}
#sidebar a {
    display: block;
    padding: 0.3rem 0.5rem;
    color: #c8d6f0;
    text-decoration: none;
    border-radius: 4px;
    font-size: 0.875rem;
}
#sidebar a:hover, #sidebar a.active { background: #2e4a7a; color: #fff; }

#content {
    padding: 2.5rem 3rem;
    max-width: 860px;
}
h1, h2, h3, h4 { color: #1b2a4a; margin: 1.4em 0 0.5em; }
h1 { font-size: 1.75rem; border-bottom: 2px solid #dde3f0; padding-bottom: 0.4rem; }
h2 { font-size: 1.3rem; }
p  { margin-bottom: 1em; }
a  { color: #2563eb; }
a:hover { text-decoration: underline; }

img {
    max-width: 100%;
    height: auto;
    border-radius: 4px;
    margin: 0.75rem 0;
    box-shadow: 0 1px 4px rgba(0,0,0,.12);
}

table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #dde3f0; padding: 0.5rem 0.75rem; text-align: left; }
th { background: #eef2fa; font-weight: 600; }

ul, ol { padding-left: 1.5rem; margin-bottom: 1em; }
li { margin-bottom: 0.25em; }

blockquote {
    border-left: 4px solid #2563eb;
    margin: 1em 0;
    padding: 0.5rem 1rem;
    background: #f0f4ff;
    color: #333;
}

pre, code {
    font-family: "Fira Code", Consolas, monospace;
    font-size: 0.9em;
    background: #f0f4ff;
    border-radius: 3px;
    padding: 0.1em 0.4em;
}
pre { padding: 1em; overflow-x: auto; }

.breadcrumb { font-size: 0.8rem; color: #666; margin-bottom: 1.5rem; }
.breadcrumb a { color: #2563eb; text-decoration: none; }
.breadcrumb a:hover { text-decoration: underline; }

.ext-link::after { content: " \\2197"; font-size: 0.8em; opacity: 0.6; }

.img-missing {
    display: inline-block;
    background: #f0f4ff;
    border: 1px dashed #aab8d4;
    color: #666;
    font-size: 0.8em;
    padding: 0.25rem 0.6rem;
    border-radius: 3px;
    margin: 0.25rem 0;
    vertical-align: middle;
}

.bb-embedded {
    border: 1px solid #dde3f0;
    border-radius: 6px;
    padding: 1rem 1.25rem;
    margin: 1.25rem 0;
    background: #fafbff;
    overflow-x: auto;
}

@media (max-width: 700px) {
    body { grid-template-columns: 1fr; }
    #sidebar { position: static; height: auto; }
    #content { padding: 1.5rem; }
}
"""

# ── HTML LINK REWRITER ────────────────────────────────────────────────────────

class LinkRewriter(HTMLParser):
    """
    Single-pass rewriter that:
      - removes BB chrome tags
      - rewrites src/href attributes to relative paths
      - marks external links with a CSS class
      - collects all asset references for extraction
    """

    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, page_zip_path: str, asset_map: dict, site_dir: Path):
        super().__init__(convert_charrefs=False)
        self._page_path  = PurePosixPath(page_zip_path)
        self._asset_map  = asset_map   # lowercase filename -> zip path
        self._site_dir   = site_dir
        self._page_site  = None        # absolute Path of output file
        self.out         : list[str] = []
        self.asset_refs  : list[str] = []
        self._skip_stack : list[str] = []

    def set_page_site_path(self, p: Path):
        self._page_site = p

    def _should_skip(self, tag: str, attr_dict: dict) -> bool:
        if tag in ("script", "style", "noscript"):
            return True
        if tag == "link" and attr_dict.get("rel", "").lower() in ("stylesheet", "icon"):
            return True
        cls = attr_dict.get("class", "").lower()
        id_ = attr_dict.get("id",    "").lower()
        combined = cls + " " + id_
        return any(frag in combined for frag in BB_CHROME_IDS_CLASSES)

    def _rewrite_url(self, url: str) -> tuple[str, str | None]:
        """Returns (new_url, zip_asset_path_or_None)."""
        if not url or url.startswith(("javascript:", "mailto:", "data:", "#")):
            return url, None

        # BB sometimes emits malformed HTML where the src closing quote is missing,
        # causing subsequent attributes to be concatenated into the URL value.
        # Trim at the first whitespace to recover the actual URL.
        if " " in url:
            url = url.split()[0]

        parsed = urlparse(url)

        if parsed.scheme in ("http", "https"):
            # Try to match by filename to something in the ZIP
            filename = Path(unquote(parsed.path)).name.lower()
            if filename and filename in self._asset_map:
                zip_p = self._asset_map[filename]
                return self._rel_to_page(zip_p), zip_p
            return url, None   # genuine external link

        # Relative URL — resolve against page position
        raw = unquote(parsed.path)
        resolved = (self._page_path.parent / raw).as_posix()
        parts = []
        for seg in resolved.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg and seg != ".":
                parts.append(seg)
        zip_p = "/".join(parts)

        if zip_p in self._asset_map.values():
            return self._rel_to_page(zip_p), zip_p

        # Fallback — keep as-is
        return url, None

    def _rel_to_page(self, zip_asset_path: str) -> str:
        if self._page_site is None:
            return zip_asset_path
        asset_abs = self._site_dir / zip_asset_path
        try:
            return os.path.relpath(asset_abs, self._page_site.parent).replace("\\", "/")
        except ValueError:
            return "/" + zip_asset_path

    def handle_starttag(self, tag: str, attrs):
        # Filter out malformed attribute names (e.g. `"` from missing closing quotes in BB HTML)
        attr_dict = {k: v for k, v in attrs if k and '"' not in k and "'" not in k}

        if self._skip_stack:
            if tag not in self.VOID_TAGS:
                self._skip_stack.append(tag)
            return

        if self._should_skip(tag, attr_dict):
            if tag not in self.VOID_TAGS:
                self._skip_stack.append(tag)
            return

        for attr in ("src", "href", "data", "poster"):
            if attr in attr_dict:
                new_url, zip_p = self._rewrite_url(attr_dict[attr])
                attr_dict[attr] = new_url
                if zip_p:
                    self.asset_refs.append(zip_p)

        # Images with unresolvable external src (e.g. BB server images not in ZIP):
        # replace with a visible placeholder so the user knows content was here.
        if tag == "img" and "src" in attr_dict:
            src = attr_dict["src"]
            if src.startswith(("http://", "https://")):
                attr_dict["onerror"] = (
                    "this.onerror=null;"
                    "var d=document.createElement('span');"
                    "d.className='img-missing';"
                    "d.textContent='[image not in export]';"
                    "this.parentNode.replaceChild(d,this)"
                )

        if tag == "a" and "href" in attr_dict:
            href = attr_dict["href"]
            if href.startswith(("http://", "https://")):
                attr_dict["class"]  = (attr_dict.get("class", "") + " ext-link").strip()
                attr_dict["target"] = "_blank"
                attr_dict["rel"]    = "noopener noreferrer"

        attrs_str = " ".join(
            f'{k}="{v}"' if v is not None else k
            for k, v in attr_dict.items()
        )
        self.out.append(f"<{tag} {attrs_str}>" if attrs_str else f"<{tag}>")

    def handle_endtag(self, tag: str):
        if self._skip_stack:
            if self._skip_stack[-1] == tag:
                self._skip_stack.pop()
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str):
        if not self._skip_stack:
            self.out.append(data)

    def handle_entityref(self, name: str):
        if not self._skip_stack:
            self.out.append(f"&{name};")

    def handle_charref(self, name: str):
        if not self._skip_stack:
            self.out.append(f"&#{name};")

    def get_output(self) -> str:
        return "".join(self.out)


# ── MANIFEST + .dat HELPERS ───────────────────────────────────────────────────

import html as _html_lib
import xml.etree.ElementTree as ET


def preprocess_bb_urls(html_str: str, bb_server: str = "https://bb.local") -> str:
    """
    Replace BB @X@ URL tokens so the LinkRewriter can handle them normally.
    - unsafeHtml refs → sentinel scheme (handled by inline_embedded_html)
    - requestUrlStub bbcswebdav refs → real server URL (or bb.local fallback)
    - all other @X@ refs → dead link
    """
    html_str = re.sub(
        r'@X@EmbeddedFile\.unsafeHtml@X@(bbcswebdav/[^"\'<>\s]+)',
        r'unsafe-html://\1',
        html_str,
    )
    html_str = re.sub(
        r'@X@EmbeddedFile\.requestUrlStub@X@(bbcswebdav/[^"\'<>\s]+)',
        rf'{bb_server}/\1',
        html_str,
    )
    html_str = re.sub(r'@X@[^"\'<>\s]+', '#', html_str)
    return html_str


def inline_embedded_html(html_str: str, zf, csfiles_html_map: dict) -> str:
    """
    Find <a data-bbtype="embedded-unsafe-html"> elements and replace them
    with the body content of the referenced csfiles HTML file.
    Falls back to removing the anchor if the file is not found.
    """
    def replacer(m):
        full_tag = m.group(0)
        href_m = re.search(r'href=["\']([^"\']+)["\']', full_tag)
        if not href_m:
            return ""
        xid_m = re.search(r"xid-(\d+)_\d+", href_m.group(1))
        if not xid_m or xid_m.group(1) not in csfiles_html_map:
            return ""
        try:
            raw = zf.read(csfiles_html_map[xid_m.group(1)]).decode("utf-8", errors="replace")
            # Strip the BB auto-resize script header
            raw = re.sub(
                r'<script[^>]*class=["\']bb-embedded-html[^>]*>.*?</script>',
                "", raw, flags=re.DOTALL | re.I,
            )
            body_m = re.search(r"<body[^>]*>(.*?)</body>", raw, re.DOTALL | re.I)
            body = body_m.group(1).strip() if body_m else raw
            # Normalise any BB @X@ tokens inside inlined HTML
            body = preprocess_bb_urls(body)
            return f'<div class="bb-embedded">{body}</div>'
        except Exception:
            return ""

    return re.sub(
        r'<a\b[^>]*data-bbtype=["\']embedded-unsafe-html["\'][^>]*>.*?</a>',
        replacer,
        html_str,
        flags=re.DOTALL | re.I,
    )


def convert_bbfile_images(raw_html: str) -> str:
    """
    Convert Blackboard file-attachment anchors that carry image mime types to
    <img> tags.  Must be called on the raw (still HTML-entity-encoded) TEXT
    content from the .dat file, before html.unescape() is applied.

    BB encodes these as:
      <a data-bbfile="{&quot;mimeType&quot;:&quot;image/png&quot;,...}"
         href="@X@EmbeddedFile.requestUrlStub@X@bbcswebdav/xid-NNN_1"></a>

    After conversion the @X@ href flows through preprocess_bb_urls() and then
    the LinkRewriter resolves the xid to the local asset path.
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


def read_dat_html(zf, dat_path: str, bb_server: str = "https://bb.local") -> tuple[str, str]:
    """
    Read a BB .dat file from the open ZipFile.
    Returns (title, unescaped_html_content).
    Both are empty strings if the file is missing or not a document type.
    """
    try:
        raw = zf.read(dat_path)
    except KeyError:
        return "", ""

    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return "", ""

    handler_el = root.find("CONTENTHANDLER")
    handler = handler_el.get("value", "") if handler_el is not None else ""
    if handler != "resource/x-bb-document":
        return "", ""

    title_el = root.find("TITLE")
    title = title_el.get("value", "").strip() if title_el is not None else ""

    body_el = root.find("BODY")
    text_el = body_el.find("TEXT") if body_el is not None else None
    raw_html = text_el.text if text_el is not None and text_el.text else ""
    if raw_html:
        # Convert bbfile image anchors before unescaping so &quot; delimiters
        # inside the JSON stay intact for the regex match
        raw_html = convert_bbfile_images(raw_html)
    html_content = preprocess_bb_urls(_html_lib.unescape(raw_html), bb_server) if raw_html else ""

    return title, html_content


# ── MANIFEST PARSER ───────────────────────────────────────────────────────────

def parse_manifest(xml_bytes: bytes) -> list[dict]:
    """
    Parse BB Ultra imsmanifest.xml into an ordered TOC.

    Each item carries:
      title          - manifest title
      ref            - identifierref (e.g. "res00274")
      dat_file       - "res00274.dat"
      type           - BB resource type string
      depth          - nesting depth (normalised, shallowest = 0)
      is_section     - True for folders/lessons that are nav headings
      nav_title      - display title ("ultraDocumentBody" items use parent title)
      module         - depth-2 section name used for breadcrumbs
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    def strip_ns(tag):
        return tag.split("}")[-1] if "}" in tag else tag

    # Resource type map: identifier → type
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
            rtype = res_types.get(ref, "")
            if title and ref:
                items.append({
                    "title":      title,
                    "ref":        ref,
                    "dat_file":   ref + ".dat",
                    "type":       rtype,
                    "depth":      depth,
                    "is_section": rtype != "resource/x-bb-document",
                })
        for child in node:
            walk(child, depth + 1)

    walk(root)
    if not items:
        return items

    min_depth = min(i["depth"] for i in items)
    for i in items:
        i["depth"] -= min_depth

    # Assign nav_title and module using a depth-tracking stack
    depth_titles: dict[int, str] = {}
    for item in items:
        d = item["depth"]
        title = item["title"]
        depth_titles[d] = title
        for k in list(depth_titles):
            if k > d:
                del depth_titles[k]

        # Module: depth-2 ancestor (top course section), skip generic names
        module = depth_titles.get(2) or depth_titles.get(1) or "General"
        if module.lower() in ("root", "--top--"):
            module = depth_titles.get(3) or "General"
        item["module"] = module

        # nav_title: "ultraDocumentBody" items display their parent's title
        if title.lower() in ("ultradocumentbody", ""):
            parent = depth_titles.get(d - 1, title)
            if parent.lower() in ("ultradocumentbody", "root", "--top--"):
                parent = depth_titles.get(d - 2, title)
            item["nav_title"] = parent
        else:
            item["nav_title"] = title

    return items


# ── PAGE TEMPLATE ─────────────────────────────────────────────────────────────

def wrap_page(title: str, body_html: str, breadcrumb: str,
              nav_html: str, depth: int) -> str:
    css_rel = "../" * depth + "assets/style.css"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="{css_rel}">
</head>
<body>
  <nav id="sidebar">
    <h1>Course Content</h1>
    {nav_html}
  </nav>
  <main id="content">
    <p class="breadcrumb">{breadcrumb}</p>
    {body_html}
  </main>
</body>
</html>"""


# ── NAV BUILDER ───────────────────────────────────────────────────────────────

def build_nav_html(toc: list[dict], page_by_ref: dict,
                   current_ref: str | None, site_dir: Path) -> str:
    """
    Build sidebar HTML from the manifest TOC and a lookup of built pages.

    Section-hood is inferred from page_by_ref rather than the manifest type
    attribute (BB Ultra marks all resources as the same type at the manifest
    level; the real type lives inside each .dat file).

    - Items at depth 2 with no built page → module headings
    - Items with a built page → clickable links
    - Deeper items with no page (container nodes) → skipped
    """
    parts = []

    for item in toc:
        depth = item["depth"]
        title = item["title"]

        # Skip root-level boilerplate
        if depth < 2 or title.lower() in ("root", "--top--"):
            continue

        pg = page_by_ref.get(item["ref"])

        if pg is None:
            # No content for this item
            if depth == 2:
                label = title[:60] + ("…" if len(title) > 60 else "")
                parts.append(f'<p class="module-title">{label}</p>')
            # Deeper container nodes (e.g. parent folders) → skip
            continue

        # Built page → clickable link
        nav_title = item["nav_title"]
        label = nav_title[:60] + ("…" if len(nav_title) > 60 else "")

        href = pg["site_path"]
        if current_ref and current_ref in page_by_ref:
            cur_path = page_by_ref[current_ref]["site_path"]
            try:
                href = os.path.relpath(
                    site_dir / pg["site_path"],
                    (site_dir / cur_path).parent,
                ).replace("\\", "/")
            except ValueError:
                href = pg["site_path"]

        active = ' class="active"' if item["ref"] == current_ref else ""
        indent = ' style="padding-left:0.75rem"' if depth > 3 else ""
        parts.append(f'<a href="{href}"{active}{indent}>{label}</a>')

    return "\n".join(parts)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run():
    if not INVENTORY_FILE.exists():
        print("❌  Run step1_inspect_zip.py first.")
        return

    with open(INVENTORY_FILE, encoding="utf-8") as f:
        inventory = json.load(f)

    zip_path = Path(inventory["zip_path"])
    print(f"\n🌐  Building static site from {zip_path.name} ...\n")

    # ── 1. Scan ZIP: assets, manifest, csfiles HTML ───────────────────────────
    asset_by_name: dict[str, str] = {}   # lowercase filename/stem → zip path
    asset_paths: set[str]         = set()
    manifest_bytes: bytes | None  = None
    csfiles_html_map: dict[str, str] = {}  # xid_num → zip path
    bb_server = "https://bb.local"         # overridden once we detect the hostname

    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            name  = info.filename
            ext   = Path(name).suffix.lower()
            fname = Path(name).name.lower()

            if ext in ASSET_EXTS:
                asset_paths.add(name)
                if fname not in asset_by_name:
                    asset_by_name[fname] = name
                # xid stem index: "__xid-15654259_1.png" → "xid-15654259_1"
                stem_clean = fname.lstrip("_").rsplit(".", 1)[0]
                if stem_clean and stem_clean not in asset_by_name:
                    asset_by_name[stem_clean] = name

            if fname in ("imsmanifest.xml", "bb_manifest.xml", "tincan.xml", "cmi5.xml"):
                manifest_bytes = zf.read(name)

            # Track csfiles HTML for inline embedding; detect BB server hostname
            if ext == ".html" and "csfiles" in name.lower():
                m = re.search(r"xid-(\d+)_\d+\.html$", name, re.I)
                if m:
                    csfiles_html_map[m.group(1)] = name
                if bb_server == "https://bb.local" and len(csfiles_html_map) <= 5:
                    try:
                        sample = zf.read(name).decode("utf-8", errors="replace")
                        sm = re.search(r'(https?://[a-zA-Z0-9._:-]+)/bbcswebdav/', sample)
                        if sm:
                            bb_server = sm.group(1)
                    except Exception:
                        pass

    toc       = parse_manifest(manifest_bytes) if manifest_bytes else []
    doc_items = [i for i in toc if not i["is_section"]]

    print(f"   Manifest items : {len(toc)}")
    print(f"   Document pages : {len(doc_items)}")
    print(f"   Assets         : {len(asset_paths)}")
    print(f"   Embedded HTML  : {len(csfiles_html_map)}")

    # ── 2. Prepare output directory ───────────────────────────────────────────
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    ASSETS_DIR.mkdir(parents=True)
    (ASSETS_DIR / "style.css").write_text(STYLESHEET, encoding="utf-8")

    # ── 3. First pass: build page list (skip items with no content) ───────────
    pages: list[dict] = []
    page_by_ref: dict[str, dict] = {}

    with zipfile.ZipFile(zip_path, "r") as zf:
        available = set(zf.namelist())
        for item in doc_items:
            if item["dat_file"] not in available:
                continue
            _, html_content = read_dat_html(zf, item["dat_file"], bb_server)
            if not html_content.strip():
                continue

            pg = {
                "ref":       item["ref"],
                "dat_file":  item["dat_file"],
                "site_path": item["ref"] + ".html",   # flat layout
                "nav_title": item["nav_title"],
                "module":    item["module"],
                "depth":     item["depth"],
            }
            pages.append(pg)
            page_by_ref[item["ref"]] = pg

    print(f"   Pages with content: {len(pages)}")

    # ── 4. Second pass: render pages ──────────────────────────────────────────
    copied_assets: set[str] = set()

    with zipfile.ZipFile(zip_path, "r") as zf:
        for i, pg in enumerate(pages):
            _, html_content = read_dat_html(zf, pg["dat_file"])

            # Inline embedded HTML components before link rewriting
            html_content = inline_embedded_html(html_content, zf, csfiles_html_map)

            out_path = SITE_DIR / pg["site_path"]
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # LinkRewriter treats the page as if it sits at the ZIP root
            rewriter = LinkRewriter(pg["ref"], asset_by_name, SITE_DIR)
            rewriter.set_page_site_path(out_path)
            rewriter.feed(html_content)
            body_html = rewriter.get_output()

            # Copy assets referenced by this page
            for asset_zip_path in rewriter.asset_refs:
                if asset_zip_path in copied_assets:
                    continue
                try:
                    data = zf.read(asset_zip_path)
                    dest = SITE_DIR / asset_zip_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    copied_assets.add(asset_zip_path)
                except KeyError:
                    pass

            nav_html   = build_nav_html(toc, page_by_ref, pg["ref"], SITE_DIR)
            breadcrumb = (
                f'<a href="index.html">Home</a>'
                f" \u203a {pg['module']}"
                f" \u203a {pg['nav_title']}"
            )

            out_path.write_text(
                wrap_page(pg["nav_title"], body_html, breadcrumb, nav_html, depth=0),
                encoding="utf-8",
            )

            if (i + 1) % 50 == 0:
                print(f"   {i + 1}/{len(pages)} pages processed ...")

        # ── 5. Copy ALL remaining images (central asset pools) ────────────────
        print("   Copying remaining image assets ...")
        for asset_zip_path in asset_paths:
            if asset_zip_path in copied_assets:
                continue
            if Path(asset_zip_path).suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                data = zf.read(asset_zip_path)
                dest = SITE_DIR / asset_zip_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                copied_assets.add(asset_zip_path)
            except KeyError:
                pass

    # ── 6. Build index.html ───────────────────────────────────────────────────
    # Group pages by module in manifest order
    seen_modules: list[str] = []
    by_module: dict[str, list] = defaultdict(list)
    for pg in pages:
        m = pg["module"]
        if m not in by_module:
            seen_modules.append(m)
        by_module[m].append(pg)

    index_body = ["<h1>Course Contents</h1>"]
    for module in seen_modules:
        index_body.append(f"<h2>{module}</h2><ul>")
        for pg in by_module[module]:
            index_body.append(
                f'<li><a href="{pg["site_path"]}">{pg["nav_title"]}</a></li>'
            )
        index_body.append("</ul>")

    (SITE_DIR / "index.html").write_text(
        wrap_page(
            title      = "Course Contents",
            body_html  = "\n".join(index_body),
            breadcrumb = "Home",
            nav_html   = build_nav_html(toc, page_by_ref, None, SITE_DIR),
            depth      = 0,
        ),
        encoding="utf-8",
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    site_size = sum(f.stat().st_size for f in SITE_DIR.rglob("*") if f.is_file())
    print(f"\n✅  Static site built!")
    print(f"   Pages    : {len(pages)}")
    print(f"   Assets   : {len(copied_assets)}")
    print(f"   Size     : {site_size / (1024 * 1024):.1f} MB")
    print(f"   Location : {SITE_DIR.resolve()}")
    print(f"\n   Open: {(SITE_DIR / 'index.html').resolve()}\n")


if __name__ == "__main__":
    run()
