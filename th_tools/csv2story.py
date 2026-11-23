"""Put Thai translations from TStudio CSVs back into story/<ep>/wh/*.txt.

For every th_work/csv/<ep>/<name>.umimeta.json it reads the translations from
<name>_translated.csv (TStudio Batch output) or, if that does not exist, from the
Translation column of <name>.csv, and rewrites the story file the meta points to.

Safety rules per line:
  * empty translation             -> keep the English line
  * tags {..} differ from English -> keep the English line (reported as TAG)
  * opening/closing double quotes and the trailing space of the English line are
    restored automatically (AI often drops them; the game needs them)
  * backticks in the translation are replaced with ' so the script cannot break

Usage (from the repo root):
    python th_tools/csv2story.py ep1                 # whole episode
    python th_tools/csv2story.py ep1 --only umi1_op  # just some files
    python th_tools/csv2story.py ep1 --check         # report only, write nothing
"""
import argparse, csv, glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_DIR = os.path.join(ROOT, "th_work", "csv")
TAG_RE = re.compile(r"\{([a-z]+)(:)?")
QUOTES = '"\u201c\u201d'


def tag_shape(s):
    return (TAG_RE.findall(s), s.count("{"), s.count("}"))


def read_csv(path, col):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r.get("ID", ""): (r.get(col) or "") for r in csv.DictReader(f)}


def fix_line(src, tr):
    core = tr.strip().replace("`", "'")
    s_core = src.strip()
    trailing = src[len(src.rstrip()):]
    leading = src[: len(src) - len(src.lstrip())]
    if s_core.startswith('"'):
        core = '"' + core.lstrip(QUOTES)
    else:
        core = core.lstrip(QUOTES) if core[:1] in QUOTES and not s_core[:1] in QUOTES else core
    if s_core.endswith('"') and len(s_core) > 1:
        core = core.rstrip(QUOTES) + '"'
    elif core[-1:] in QUOTES and s_core[-1:] not in QUOTES:
        core = core.rstrip(QUOTES)
    return leading + core + trailing


def process(meta_path, write):
    base = meta_path[: -len(".umimeta.json")]
    name = os.path.basename(base)
    meta = json.load(open(meta_path, encoding="utf-8"))
    src = read_csv(base + ".csv", "Source")
    tr = read_csv(base + "_translated.csv", "Translation") or read_csv(base + ".csv", "Translation")
    stats = dict(total=0, thai=0, english=0, tag=0)
    bad = []
    out = []
    for l in meta["lines"]:
        if "raw" in l:
            out.append(l["raw"]); continue
        s = src[l["id"]]
        t = tr.get(l["id"], "")
        stats["total"] += 1
        if not t.strip():
            text = s; stats["english"] += 1
        elif tag_shape(s) != tag_shape(t):
            text = s; stats["tag"] += 1; bad.append(l["id"])
        else:
            text = fix_line(s, t); stats["thai"] += 1
        out.append(l["pre"] + text + l["post"])
    data = meta["newline"].join(out) + (meta["newline"] if meta["final_newline"] else "")
    if write:
        with open(os.path.join(ROOT, meta["source"]), "wb") as f:
            f.write(data.encode("utf-8"))
    return name, stats, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", nargs="+", help="ep1 ... ep8, omake")
    ap.add_argument("--only", nargs="*", help="file names without .txt, e.g. umi1_op")
    ap.add_argument("--check", action="store_true", help="report only, do not write")
    a = ap.parse_args()
    for ep in a.episodes:
        metas = sorted(glob.glob(os.path.join(CSV_DIR, ep, "*.umimeta.json")))
        if not metas:
            print(f"!! {ep}: no CSVs found, run story2csv.py first"); continue
        for mp in metas:
            nm = os.path.basename(mp)[: -len(".umimeta.json")]
            if a.only and nm not in a.only:
                continue
            name, st, bad = process(mp, not a.check)
            if st["thai"] == 0 and not bad:
                continue
            msg = f"{ep}/{name}: thai {st['thai']}/{st['total']}, english {st['english']}, tag-mismatch {st['tag']}"
            print(msg)
            for b in bad[:20]:
                print(f"   TAG kept English: {b}")
    print("check only, nothing written" if a.check else "done")


if __name__ == "__main__":
    sys.exit(main())