"""Rewrite the Source column of the CSVs so TStudio can translate text inside tags.

    {p:1:I acknowledge it.}   ->  {p:1}I acknowledge it.{/p}
    {i:she had}               ->  {i}she had{/i}
    {f:5:Beatrice}            ->  {f:5}Beatrice{/f}
    {ruby:Gospel:Fukuin}      ->  {ruby}Gospel{rb}Fukuin{/ruby}
    <Good>                    ->  {en}Good{/en}

(details in tagsplit.py).  csv2story.py / csv2menu.py / qa_check.py understand both
forms and always write the original game syntax back, so this is only about what
TStudio sees.  IDs (with the @speaker tags) and the other columns stay as they are.

Usage (from the repo root):
    python th_tools/split_tags.py                  # all episodes + script
    python th_tools/split_tags.py ep1 script       # some targets
    python th_tools/split_tags.py ep1 --only umi1_op
    python th_tools/split_tags.py --check          # report only, write nothing
    python th_tools/split_tags.py --strip          # back to the original {p:1:..} form

Re-running is safe (the result is always split(join(Source))).  Files that already have
<name>_translated.csv, or any filled Translation / AI_Reference cell, are skipped:
their Source must stay exactly as TStudio saw it.  Every row is checked:
join(new Source) must equal join(old Source), otherwise the row is left unchanged.
"""
import argparse, csv, glob, io, os, re, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tagsplit

ROOT = os.path.dirname(HERE)
CSV_DIR = os.path.join(ROOT, "th_work", "csv")
TAG_RE = re.compile(r"(<[^>]+>|\{[^}\r\n]+\})")          # what TStudio hides (simplified)


def hidden_letters(s):
    return len(re.findall(r"[A-Za-z]", TAG_RE.sub("", s)))


def process(csv_path, script, args):
    base = csv_path[:-4]
    name = os.path.basename(base)
    if os.path.exists(base + "_translated.csv"):
        return name, None, "skipped: " + name + "_translated.csv exists (Source must stay as TStudio saw it)"
    raw = open(csv_path, "rb").read()
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    header, body = rows[0], rows[1:]
    if header[:2] != ["ID", "Source"]:
        return name, None, "skipped: not an ID/Source CSV"
    if any(any(c.strip() for c in r[2:]) for r in body):
        return name, None, "skipped: Translation/AI_Reference already filled"
    st, ctx = Counter(rows=len(body)), []
    for r in body:
        old = r[1]
        orig = tagsplit.join(old)
        if script:
            ctx = []
        new = orig if args.strip else tagsplit.split(orig, ctx)
        if tagsplit.join(new) != orig or (not args.strip and tagsplit.structure(new) is None):
            st["kept_unsafe"] += 1
            continue
        if new != old:
            st["rows_changed"] += 1
            r[1] = new
        if not args.strip and hidden_letters(new) > hidden_letters(orig):
            st["rows_text_exposed"] += 1
    buf = io.StringIO(newline="")
    w = csv.writer(buf)            # same writer settings as story2csv.py / add_context.py
    w.writerow(header)
    w.writerows(body)
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    changed = data != raw
    if changed and not args.check:
        tmp = csv_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, csv_path)
    st["changed_files"] += int(changed)
    return name, st, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="*", help="ep1 ... ep8, omake, script (default: all)")
    ap.add_argument("--only", nargs="*", help="file names without .csv, e.g. umi1_op menu")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    ap.add_argument("--strip", action="store_true", help="back to the original tag syntax")
    a = ap.parse_args()
    targets = a.targets or sorted(d for d in os.listdir(CSV_DIR) if os.path.isdir(os.path.join(CSV_DIR, d)))
    total = Counter()
    for t in targets:
        files = sorted(f for f in glob.glob(os.path.join(CSV_DIR, t, "*.csv"))
                       if not f.endswith("_translated.csv") and os.path.exists(f[:-4] + ".umimeta.json"))
        if not files:
            print(f"!! {t}: no CSVs found"); continue
        tst = Counter()
        for f in files:
            nm = os.path.basename(f)[:-4]
            if a.only and nm not in a.only:
                continue
            name, st, msg = process(f, t == "script", a)
            if msg:
                print(f"   {t}/{name}: {msg}"); continue
            tst.update(st)
        total.update(tst)
        print(f"{t}: " + ", ".join(f"{k} {v}" for k, v in sorted(tst.items())))
    print("TOTAL: " + ", ".join(f"{k} {v}" for k, v in sorted(total.items())))
    if total["kept_unsafe"]:
        print("!! some rows could not be split safely and were left as they are")
    print("check only, nothing written" if a.check else "done")


if __name__ == "__main__":
    sys.exit(main())
