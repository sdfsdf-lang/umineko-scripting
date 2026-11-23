"""Put Thai translations from TStudio CSVs back into script/wh/menu.txt and header.txt.

For th_work/csv/script/<name>.umimeta.json it reads the translations from
<name>_translated.csv (TStudio Batch output) or, if that does not exist, from the
Translation column of <name>.csv, and rebuilds the script file from the meta
(the meta holds the original English file, so running this again is always safe).

Safety rules per string:
  * empty translation             -> keep the English
  * tags {..} differ from English -> keep the English (reported as TAG)
  * leading/trailing spaces of the English string are restored
  * " would end the NScripter string -> turned into curly quotes “ ”
  * ` (sprite/text separator)        -> turned into '
  * real line breaks                 -> {n}
  * split tags ({p:1}text{/p}, see tagsplit.py) are joined back to {p:1:text}

Usage (from the repo root):
    python th_tools/csv2menu.py               # menu + header
    python th_tools/csv2menu.py menu          # only menu.txt
    python th_tools/csv2menu.py --check       # report only, write nothing
"""
import argparse, csv, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tagsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_DIR = os.path.join(ROOT, "th_work", "csv", "script")
TAG_RE = re.compile(r"\{([a-z]+)(:)?")


def tag_shape(s):
    return (TAG_RE.findall(s), s.count("{"), s.count("}"))


def read_csv(path, col):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r.get("ID", ""): (r.get(col) or "") for r in csv.DictReader(f)}


def clean(tr):
    t = tr.strip().replace("\r\n", "{n}").replace("\n", "{n}").replace("\r", "{n}")
    t = t.replace("`", "'")
    out, open_q = [], True
    for ch in t:                               # straight " -> “ ” alternately
        if ch == '"':
            out.append("\u201c" if open_q else "\u201d"); open_q = not open_q
        else:
            out.append(ch)
    return "".join(out)


def fix(src, tr):
    leading = src[: len(src) - len(src.lstrip())]
    trailing = src[len(src.rstrip()):]
    return leading + clean(tr) + trailing


def rebuild(meta, texts):
    out = []
    for l in meta["lines"]:
        if "raw" in l:
            out.append(l["raw"]); continue
        out.append("".join(p if isinstance(p, str) else p["pre"] + texts[p["id"]] + p["post"] for p in l["parts"]))
    return meta["newline"].join(out) + (meta["newline"] if meta["final_newline"] else "")


def process(name, write):
    base = os.path.join(CSV_DIR, name)
    meta = json.load(open(base + ".umimeta.json", encoding="utf-8"))
    src = read_csv(base + ".csv", "Source")
    tr = read_csv(base + "_translated.csv", "Translation") or read_csv(base + ".csv", "Translation")
    st = dict(total=0, thai=0, english=0, tag=0)
    bad, texts = [], {}
    for rid, s in src.items():
        t = tr.get(rid, "")
        st["total"] += 1
        if not t.strip():
            texts[rid] = tagsplit.join(s); st["english"] += 1
        elif not tagsplit.tags_ok(s, clean(t)):
            texts[rid] = tagsplit.join(s); st["tag"] += 1; bad.append(rid)
        else:
            texts[rid] = tagsplit.join(fix(s, t)); st["thai"] += 1
    target = os.path.join(ROOT, meta["source"])
    english = rebuild(meta, {k: tagsplit.join(v) for k, v in src.items()}).encode("utf-8")
    data = rebuild(meta, texts).encode("utf-8")
    current = open(target, "rb").read() if os.path.exists(target) else b""
    print(f"script/{name}: thai {st['thai']}/{st['total']}, english {st['english']}, tag-mismatch {st['tag']}")
    for b in bad[:30]:
        print(f"   TAG kept English: {b}")
    if len(bad) > 30:
        print(f"   ... and {len(bad) - 30} more")
    if current == english:
        print(f"   {meta['source']} is currently the original English (round-trip OK)")
    elif current == data:
        print(f"   {meta['source']} already contains exactly these translations")
    else:
        print(f"   {meta['source']} differs from the English original (translated earlier or edited by hand)")
    if write:
        with open(target, "wb") as f:
            f.write(data)
        print(f"   wrote {meta['source']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="menu, header (default: both)")
    ap.add_argument("--check", action="store_true", help="report only, do not write")
    a = ap.parse_args()
    for n in a.names or ["menu", "header"]:
        if not os.path.exists(os.path.join(CSV_DIR, n + ".umimeta.json")):
            print(f"!! script/{n}: no CSV found, run menu2csv.py first"); continue
        process(n, not a.check)
    print("check only, nothing written" if a.check else "done")


if __name__ == "__main__":
    sys.exit(main())
