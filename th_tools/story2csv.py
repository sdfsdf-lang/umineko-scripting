"""Convert Umineko Witch-Hunt story files (story/<ep>/wh/*.txt) to TStudio CSVs.

Each story line looks like:  `text with {tags}`<space>
Only the text between the backticks goes into the CSV (column Source).
Everything needed to rebuild the file exactly (backtick wrappers, spaces,
blank lines, line endings) is saved next to the CSV in <name>.umimeta.json.

Usage (from the repo root):
    python th_tools/story2csv.py              # all episodes
    python th_tools/story2csv.py ep1 ep2      # only these episodes
    python th_tools/story2csv.py --force ep1  # overwrite existing CSVs

Output: th_work/csv/<ep>/<name>.csv  (UTF-8 with BOM, columns ID,Source,Translation,AI_Reference)
Existing CSVs are skipped unless --force, so translations are never overwritten by accident.
"""
import argparse, csv, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORY = os.path.join(ROOT, "story")
OUT = os.path.join(ROOT, "th_work", "csv")
LINE_RE = re.compile(r"^(\s*`)(.*)(`\s*)$", re.S)


def convert(ep, path, force):
    name = os.path.splitext(os.path.basename(path))[0]
    out_dir = os.path.join(OUT, ep)
    csv_path = os.path.join(out_dir, name + ".csv")
    meta_path = os.path.join(out_dir, name + ".umimeta.json")
    if os.path.exists(csv_path) and not force:
        return name, None
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    final_newline = bool(text) and lines[-1] == ""
    if final_newline:
        lines = lines[:-1]

    rows, meta_lines = [], []
    for i, line in enumerate(lines, 1):
        m = LINE_RE.match(line)
        if m and m.group(2).strip():
            rid = f"{ep}/{name}:{i:05d}"
            rows.append([rid, m.group(2), "", ""])
            meta_lines.append({"id": rid, "pre": m.group(1), "post": m.group(3)})
        else:
            meta_lines.append({"raw": line})

    os.makedirs(out_dir, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Source", "Translation", "AI_Reference"])
        w.writerows(rows)
    meta = {
        "source": os.path.relpath(path, ROOT).replace("\\", "/"),
        "newline": newline,
        "final_newline": final_newline,
        "lines": meta_lines,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return name, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", nargs="*", help="ep1 ... ep8, omake (default: all)")
    ap.add_argument("--force", action="store_true", help="overwrite existing CSVs")
    a = ap.parse_args()
    eps = a.episodes or sorted(d for d in os.listdir(STORY) if os.path.isdir(os.path.join(STORY, d, "wh")))
    total_rows = total_files = skipped = 0
    for ep in eps:
        wh = os.path.join(STORY, ep, "wh")
        if not os.path.isdir(wh):
            print(f"!! {ep}: no wh folder, skipped"); continue
        ep_rows = 0
        for fn in sorted(os.listdir(wh)):
            if not fn.endswith(".txt"):
                continue
            name, n = convert(ep, os.path.join(wh, fn), a.force)
            if n is None:
                skipped += 1; print(f"   {ep}/{name}: CSV exists, skipped (use --force)")
            else:
                ep_rows += n; total_files += 1
        print(f"{ep}: {ep_rows} lines")
        total_rows += ep_rows
    print(f"Done: {total_files} files, {total_rows} lines -> {OUT}" + (f" ({skipped} skipped)" if skipped else ""))


if __name__ == "__main__":
    sys.exit(main())