"""
STEP 1 - Blackboard Ultra ZIP Inspector
Run this FIRST to understand the structure before extracting anything.

Usage:
    python step1_inspect_zip.py

Input:
    src/          <- place your Blackboard Ultra ZIP here

Output:
    outputs/bb_inventory.json   <- used by step2_extract.py
"""

import sys, zipfile, os
from collections import defaultdict
from pathlib import Path
import json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC_DIR  = Path("src")
OUT_DIR  = Path("outputs")
ZIP_PATH = next(SRC_DIR.glob("*.zip"), None)   # picks the first .zip found in src/


def inspect_zip(zip_path):
    ext_counts = defaultdict(int)
    ext_sizes  = defaultdict(int)
    html_files = []
    asset_files = []
    manifest_files = []
    all_files = []

    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            name = info.filename
            ext  = os.path.splitext(name)[1].lower()
            size = info.file_size

            ext_counts[ext] += 1
            ext_sizes[ext]  += size
            all_files.append({"path": name, "size_kb": round(size / 1024, 1), "ext": ext})

            if ext in ('.html', '.htm'):
                html_files.append(name)
            elif ext in ('.pdf', '.docx', '.pptx', '.xlsx'):
                asset_files.append({"path": name, "size_kb": round(size / 1024, 1)})
            elif 'manifest' in name.lower() or ext in ('.xml', '.json'):
                manifest_files.append(name)

    print("\n" + "=" * 60)
    print("  BLACKBOARD ULTRA ZIP STRUCTURE SUMMARY")
    print("=" * 60)
    print(f"\nSource : {zip_path}")
    print(f"Total files: {len(all_files)}")
    print(f"\n{'Extension':<15} {'Count':>8} {'Total Size':>15}")
    print("-" * 40)
    for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
        size_mb = ext_sizes[ext] / (1024 * 1024)
        print(f"{ext or '(no ext)':<15} {count:>8} {size_mb:>12.1f} MB")

    print(f"\n--- HTML Files ({len(html_files)}) ---")
    for f in html_files[:30]:
        print(f"  {f}")
    if len(html_files) > 30:
        print(f"  ... and {len(html_files) - 30} more")

    print(f"\n--- Document Assets ({len(asset_files)}) ---")
    for f in asset_files[:20]:
        print(f"  {f['path']}  ({f['size_kb']} KB)")
    if len(asset_files) > 20:
        print(f"  ... and {len(asset_files) - 20} more")

    print(f"\n--- Manifests / XML / JSON ({len(manifest_files)}) ---")
    for f in manifest_files[:20]:
        print(f"  {f}")

    OUT_DIR.mkdir(exist_ok=True)
    inventory = {
        "zip_path": str(zip_path),
        "total_files": len(all_files),
        "html_files": html_files,
        "asset_files": asset_files,
        "manifest_files": manifest_files,
        "ext_summary": {
            k: {"count": ext_counts[k], "size_mb": round(ext_sizes[k] / (1024 * 1024), 2)}
            for k in ext_counts
        }
    }

    out_path = OUT_DIR / "bb_inventory.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)

    print(f"\n✅  Inventory saved to: {out_path.resolve()}")
    print("    Run step2_extract.py next.\n")


if __name__ == "__main__":
    if ZIP_PATH is None:
        print("❌  No ZIP file found in src/  — copy your Blackboard export there first.")
    else:
        inspect_zip(ZIP_PATH)
