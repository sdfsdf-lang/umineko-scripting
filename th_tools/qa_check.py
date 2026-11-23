"""Quality check for the Thai translations before they go into the game.

Reads every CSV of the given targets, takes the translation from
<name>_translated.csv (TStudio Batch output) or, if that does not exist, from the
Translation column of <name>.csv, and writes a report:

    th_work/qa/<target>_qa.csv     (Severity, Check, ID, Source, Translation, Detail)

plus a short summary on screen.  Nothing else is changed.

Usage (from the repo root):
    python th_tools/qa_check.py ep1                    # one episode
    python th_tools/qa_check.py ep1 ep2 script         # several (script = menu.txt/header.txt)
    python th_tools/qa_check.py ep1 --only umi1_op     # only some files
    python th_tools/qa_check.py ep1 --hide-info        # leave INFO rows out of the report

Severity:  ERROR = will not reach the game (csv2story/csv2menu keep the English)
           WARN  = probably wrong, please look
           INFO  = fixed automatically or just worth knowing

Checks:
  EMPTY            no translation (a whole untranslated file is reported once as EMPTY_FILE)
  TAG_MISMATCH     tag tokens {..} differ from the English, or {x}..{/x} pairs are not
                   properly nested (same rule as csv2story/csv2menu)             ERROR
  SAME_AS_SOURCE   translation is identical to the English
  NO_THAI          translation has letters but no Thai at all
  ENGLISH_LEFT     English words left in the Thai (names in the allow-list and
                   {f:5:..} names are ignored)
  HONORIFIC        san / sama / chan / kun / dono / nee-san ... left in Latin letters
  TAG_TEXT_LEFT    the text inside a tag such as {p:1:..} (red truth), {c:..:..}, {i:..}
                   is still the English text
  LENGTH_RATIO     Thai is >3x or <0.3x the length of the English
  UNBALANCED       brackets / quotes do not match the English  ( ) [ ] 「 」 『 』 “ ” "
  BACKTICK         ` in the translation (csv2story turns it into ')               INFO
  QUOTE_LOST       leading/trailing " of a dialogue line missing (csv2story restores it) INFO
  REPEATED         the same piece of text repeated many times (AI loop)
  SAME_AS_PREVIOUS same Thai as the row above although the English differs (rows shifted?)
  GLOSSARY         English term from th_work/glossary.csv in the Source, but its Thai
                   term is not in the translation
  TOO_LONG         script only: names, {p:8:} buttons and {p:32:} hints have no auto-fit;
                   Thai noticeably wider than the English

th_work/glossary.csv (optional, UTF-8): columns English,Thai,Note.  Several accepted
Thai spellings can be given as  แบบหนึ่ง|แบบสอง.  Rows starting with # are ignored.
"""
import argparse, csv, glob, json, os, re, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import csv2story, csv2menu                       # same tag rules as the write-back tools
import tagsplit

ROOT = os.path.dirname(HERE)
CSV_DIR = os.path.join(ROOT, "th_work", "csv")
QA_DIR = os.path.join(ROOT, "th_work", "qa")
GLOSSARY = os.path.join(ROOT, "th_work", "glossary.csv")
SEV_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}

THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
THAI_MARKS = re.compile(r"[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]")   # take no width on screen
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\u2019]*[A-Za-z]|[A-Za-z]")
HONORIFIC_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:(?:o?nee|o?nii|obaa?|ojii?|aniki|aneki)[-\u2010 ]?)?"
    r"(?:san|sama|chan|kun|dono|senpai|sensei|tan)(?![A-Za-z])")
HONORIFIC_WORDS = re.compile(r"(?i)(?<![A-Za-z])(?:o?nee|o?nii)[-\u2010]?(?:san|sama|chan)?(?![A-Za-z])")
TAG_SYNTAX_RE = re.compile(r"\{[a-z]+:(?:[^:{}]*:)?|\{[a-z]+\}|\}")
SKIP_TAG_RE = re.compile(r"\{(?:f:5|p:4|p:0|ruby:[^:{}]*):[^{}]*\}")   # names / Japanese
INNER_RE = re.compile(r"\{([a-z]+):(?:([^:{}]*):)?([^{}]*)\}")
PAIRS = [("(", ")"), ("[", "]"), ("\u300c", "\u300d"), ("\u300e", "\u300f"), ("\u201c", "\u201d")]
STORY_QUOTES = '"\u201c\u201d'

# Latin words that may stay in the Thai text.  Character names come from add_context.py.
ALLOW = {"ok", "tv", "cd", "dvd", "pc", "sos", "vs", "mr", "mrs", "ms", "dr", "no", "ep",
         "am", "pm", "a.m", "p.m", "ii", "iii", "iv", "vi", "vii", "viii", "ix", "x", "xo",
         "ushiromiya", "rokkenjima", "fukuin", "kotobuki", "furudo", "hachijo", "ronoue",
         "sumadera", "kumasawa", "nanjo", "gohda", "amakusa", "okonogi", "ootsuki", "kawabata",
         "chiester", "beatrice", "bernkastel", "lambdadelta", "featherine", "virgilia"}
try:
    import add_context
    for _n, _g in add_context.CHARS.values():
        ALLOW.update(w.lower() for w in re.findall(r"[A-Z][a-z]+|[a-z]+", _n))
except Exception:
    pass


# ---- reading -------------------------------------------------------------------------
def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_target(target, only):
    """Yield (file_name, [(id, source, translation)], kind, meta_info)."""
    folder = os.path.join(CSV_DIR, target)
    names = sorted(os.path.basename(p)[:-len(".umimeta.json")]
                   for p in glob.glob(os.path.join(folder, "*.umimeta.json")))
    for name in names:
        if only and name not in only:
            continue
        base = os.path.join(folder, name)
        src_rows = read_rows(base + ".csv")
        tr_path = base + "_translated.csv"
        tr_rows = read_rows(tr_path) if os.path.exists(tr_path) else src_rows
        tr = {r.get("ID", "").split("@", 1)[0]: (r.get("Translation") or "") for r in tr_rows}
        rows = [(r["ID"].split("@", 1)[0], r.get("Source") or "", tr.get(r["ID"].split("@", 1)[0], ""))
                for r in src_rows]
        info = script_info(base) if target == "script" else {}
        yield name, rows, ("script" if target == "script" else "story"), info, os.path.exists(tr_path)


def script_info(base):
    """id -> (alias, pre) from the menu/header meta, for the TOO_LONG check."""
    out = {}
    meta = json.load(open(base + ".umimeta.json", encoding="utf-8"))
    for l in meta["lines"]:
        parts = l.get("parts") or []
        alias = parts[0] if parts and isinstance(parts[0], str) else ""
        for p in parts:
            if isinstance(p, dict) and "id" in p:
                out[p["id"]] = (alias, p.get("pre", ""))
    return out


def load_glossary():
    if not os.path.exists(GLOSSARY):
        return []
    out = []
    for r in read_rows(GLOSSARY):
        en = (r.get("English") or "").strip()
        th = (r.get("Thai") or "").strip()
        if not en or not th or en.startswith("#"):
            continue
        out.append((en, re.compile(r"(?i)(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])"),
                    [t.strip() for t in th.split("|") if t.strip()]))
    return out


# ---- helpers ---------------------------------------------------------------------------
def visible(s):
    return TAG_SYNTAX_RE.sub("", s.replace("{n}", " "))


def width(s):
    return len(THAI_MARKS.sub("", visible(s)).strip())


def english_words(t):
    t = SKIP_TAG_RE.sub(" ", t)
    t = visible(t)
    return [w for w in WORD_RE.findall(t) if w.lower().strip("'\u2019") not in ALLOW]


def balance(s):
    d = {o + c: s.count(o) - s.count(c) for o, c in PAIRS}
    d['"'] = s.count('"') % 2
    return d


def strip_outer_quotes(s):
    s = s.strip()
    if s[:1] in STORY_QUOTES:
        s = s[1:]
    if s[-1:] in STORY_QUOTES:
        s = s[:-1]
    return s


# ---- the checks --------------------------------------------------------------------------
def check_row(kind, rid, src, tr, prev, info, glossary):
    out = []
    add = lambda sev, chk, det="": out.append((sev, chk, det))
    t = tr.strip()
    if not t:
        add("WARN", "EMPTY")
        return out
    tr_cmp = tr if kind == "story" else csv2menu.clean(tr)
    if not tagsplit.tags_ok(src, tr_cmp):
        add("ERROR", "TAG_MISMATCH", "tags differ -> English is kept in the game")
    # all other checks look at the game syntax ({p:1}text{/p} -> {p:1:text})
    src, tr = tagsplit.join(src), tagsplit.join(tr)
    t = tr.strip()
    has_letters = bool(re.search(r"[A-Za-z]{2,}", visible(src)))
    if t == src.strip() and has_letters:
        add("WARN", "SAME_AS_SOURCE")
    elif re.search(r"[A-Za-z]{2,}", visible(t)) and not THAI_RE.search(t):
        add("WARN", "NO_THAI")
    else:
        words = english_words(t)
        if words:
            letters = sum(len(w) for w in words)
            total = letters + len(THAI_RE.findall(t))
            sev = "WARN" if len(words) >= 3 or letters > 0.3 * max(total, 1) else "INFO"
            add(sev, "ENGLISH_LEFT", " ".join(dict.fromkeys(words))[:120])
    hon = HONORIFIC_RE.findall(t) + HONORIFIC_WORDS.findall(t)
    if hon:
        add("WARN", "HONORIFIC", " ".join(dict.fromkeys(h.strip() for h in hon)))
    for name, param, inner in INNER_RE.findall(src):
        if name in ("f", "ruby") or (name == "p" and param in ("0", "4")):
            continue
        if re.search(r"[A-Za-z]{2,}", inner) and inner.strip() and "{" + name + ":" in tr and inner in tr:
            add("WARN", "TAG_TEXT_LEFT", "{%s:%s} still English: %s" % (name, param or "", inner[:60]))
    ws, wt = width(src), width(t)
    if ws >= 10 and (wt > 3 * ws or wt < 0.3 * ws):
        add("WARN", "LENGTH_RATIO", f"Thai {wt} / English {ws} = {wt / ws:.2f}")
    s_cmp, t_cmp = (strip_outer_quotes(src), strip_outer_quotes(t)) if kind == "story" else (src, csv2menu.clean(t))
    bs, bt = balance(s_cmp), balance(t_cmp)
    diff = [k for k in bs if bs[k] != bt[k]]
    if diff:
        add("WARN", "UNBALANCED", " ".join(diff))
    if "`" in t:
        add("INFO", "BACKTICK", "will be replaced by '")
    if kind == "story":
        sc = src.strip()
        if sc.startswith('"') and t[:1] not in STORY_QUOTES:
            add("INFO", "QUOTE_LOST", "opening quote (restored automatically)")
        if sc.endswith('"') and len(sc) > 1 and t[-1:] not in STORY_QUOTES:
            add("INFO", "QUOTE_LOST", "closing quote (restored automatically)")
    rep = re.search(r"(.{4,}?)\1{3,}", t)
    if rep and not re.search(r"(.{2,}?)\1{2,}", src):
        add("WARN", "REPEATED", rep.group(1)[:40] + " x" + str(len(rep.group(0)) // len(rep.group(1))))
    if prev and prev[1].strip() and t == prev[1].strip() and src.strip() != prev[0].strip() and len(t) >= 6:
        add("WARN", "SAME_AS_PREVIOUS", "rows may be shifted")
    for en, rx, thai in glossary:
        if rx.search(visible(src)) and not any(x in t for x in thai):
            add("WARN", "GLOSSARY", f"{en} -> {' | '.join(thai)}")
    if kind == "script" and rid in info:
        alias, pre = info[rid]
        no_fit = "{fit}" not in pre and "{fit}" not in src
        kind_ = ("name" if alias.startswith("stralias name_") else
                 "button {p:8:}" if "{p:8:" in pre or "{p:8:" in src else
                 "hint {p:32:}" if "{p:32:" in pre or "{p:32:" in src else None)
        if kind_ and no_fit:
            we = max(width(x) for x in src.split("{n}"))
            wt2 = max(width(x) for x in t.split("{n}"))
            if wt2 > we * 1.2 + 2:
                add("WARN", "TOO_LONG", f"{kind_}: Thai width {wt2} vs English {we} (no auto-fit)")
    return out


# ---- main ----------------------------------------------------------------------------------
def run_target(target, a, glossary):
    findings, stats, per_file = [], Counter(), Counter()
    rows_total = translated = files = 0
    for name, rows, kind, info, has_tr in load_target(target, a.only):
        if not rows:
            continue
        files += 1
        rows_total += len(rows)
        done = sum(1 for _, _, t in rows if t.strip())
        translated += done
        if done == 0:
            findings.append(("WARN", "EMPTY_FILE", f"{target}/{name}", "", "",
                             f"{len(rows)} rows, nothing translated yet"))
            stats[("WARN", "EMPTY")] += len(rows)
            per_file[name] += 0
            continue
        prev = None
        for rid, src, tr in rows:
            for sev, chk, det in check_row(kind, rid, src, tr, prev, info, glossary):
                stats[(sev, chk)] += 1
                if sev != "INFO" and chk != "EMPTY":
                    per_file[name] += 1
                if a.hide_info and sev == "INFO":
                    continue
                findings.append((sev, chk, rid, src, tr, det))
            prev = (src, tr)
    if files == 0:
        print(f"!! {target}: no CSVs found in th_work/csv/{target}")
        return
    os.makedirs(QA_DIR, exist_ok=True)
    out = os.path.join(QA_DIR, f"{target}_qa.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Severity", "Check", "ID", "Source", "Translation", "Detail"])
        w.writerows(findings)
    print(f"== {target}: {files} files, {rows_total} rows, translated {translated} "
          f"({100 * translated / max(rows_total, 1):.1f}%)")
    for (sev, chk), n in sorted(stats.items(), key=lambda x: (SEV_ORDER[x[0][0]], -x[1])):
        print(f"   {sev:5} {chk:17} {n}")
    worst = [(n, f) for f, n in per_file.items() if n]
    if worst:
        print("   most problems: " + ", ".join(f"{f} ({n})" for n, f in sorted(worst, reverse=True)[:8]))
    print(f"   report: {os.path.relpath(out, ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", help="ep1 ... ep8, omake, script")
    ap.add_argument("--only", nargs="*", help="file names without .csv, e.g. umi1_op menu")
    ap.add_argument("--hide-info", action="store_true", help="do not list INFO rows in the report")
    a = ap.parse_args()
    glossary = load_glossary()
    if glossary:
        print(f"glossary: {len(glossary)} terms from th_work/glossary.csv")
    for t in a.targets:
        run_target(t, a, glossary)


if __name__ == "__main__":
    sys.exit(main())
