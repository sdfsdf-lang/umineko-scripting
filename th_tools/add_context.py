"""Give the AI context: put the speaker of every story line into the CSV ID.

Why the ID and not AI_Reference?
  TStudio's Batch AI Runner (TRunWorker._process_batch in TRun/trun_app.py) sends
  each row to the model as   "<ID><TAB><Source>"   and nothing else.  The
  AI_Reference column is never sent; in the TStudio editor it is the
  "AI translation / draft" column (Guide Mode writes AI output into it and the
  "use draft" button copies it into Translation), so notes there would only get
  in the way.  The ID, however, is read by the model ("Context ID"), so this
  tool appends a short speaker tag to it:

      ep1/umi1_op:00007            ->  ep1/umi1_op:00007@Nanjo_male
      ep1/umi1_op:00003            ->  ep1/umi1_op:00003@narration
      (voiced line, name box hidden) ->  ...@unnamed_female

  csv2story.py / qa_check.py ignore everything after "@", so the story files are
  rebuilt exactly as before.  Source, Translation and AI_Reference are not touched.

Where the speaker comes from (needs story/<ep>/jp and game/main or game/omake):
  the game script game/main/<name>.txt contains every Japanese story line in
  backticks, after a "msgwnd_xxx" command (the name box the player sees) and,
  for voiced lines, a [lv 0*"NN"*...] voice command.  The Japanese story line N
  is the same line as the Witch-Hunt line N, so:
     name box set (msgwnd_but, msgwnd_bea, ...)  -> that character
     no name box but voiced                       -> unnamed_<gender of the voice actor's
                                                     characters>, or just "unnamed" for
                                                     the generic extra voices (00, 99)
     no name box, no voice                        -> narration
  Without the jp/game files it falls back to "dialogue"/"narration" from the quotes.

Usage (from the repo root):
    python th_tools/add_context.py              # all episodes
    python th_tools/add_context.py ep1 ep2      # only these
    python th_tools/add_context.py ep1 --only umi1_op
    python th_tools/add_context.py --check      # show what would change, write nothing
    python th_tools/add_context.py --strip      # remove the tags again (plain IDs)
    python th_tools/add_context.py --no-gender  # Battler instead of Battler_male

Files that already have <name>_translated.csv are skipped (changing their IDs would
make TStudio forget the finished rows).  Running it twice changes nothing.
"""
import argparse, bisect, csv, glob, io, os, re, sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_DIR = os.path.join(ROOT, "th_work", "csv")
STORY = os.path.join(ROOT, "story")
GAME = os.path.join(ROOT, "game")
SEP = "@"

# msgwnd code -> (short English name, gender m/f/None).  Names as in script/en/menu.txt
# (";Character names"), shortened to what the story calls them.
CHARS = {
    "ama": ("Amakusa", "m"), "bea": ("Beatrice", "f"), "be3": ("Beatrice", "f"), "be4": ("Beatrice", "f"),
    "ber": ("Bernkastel", "f"), "but": ("Battler", "m"), "bu3": ("Tohya", "m"), "cla": ("Krauss", "m"),
    "cor": ("Cornelia", "f"), "cur": ("Clair", "f"), "dla": ("Dlanor", "f"), "en2": ("AngeBeatrice", "f"),
    "en3": ("Yukari", "f"), "enj": ("Ange", "f"), "eri": ("Erika", "f"), "ev2": ("EvaBeatrice", "f"),
    "eva": ("Eva", "f"), "fe2": ("Ikuko", "f"), "fea": ("Featherine", "f"), "fur": ("Furfur", None),
    "gap": ("Gaap", "f"), "gen": ("Genji", "m"), "geo": ("George", "m"), "ger": ("Gertrude", "f"),
    "go2": ("GoatButlers", None), "goa": ("GoatButler", None), "goh": ("Gohda", "m"), "hid": ("Hideyoshi", "m"),
    "jes": ("Jessica", "f"), "kan": ("Kanon", "m"), "kas": ("Kasumi", "f"), "kaw": ("Kawabata", "m"),
    "kin": ("Kinzo", "m"), "kir": ("Kyrie", "f"), "ku2": ("Sabakichi", "m"), "kum": ("Kumasawa", "f"),
    "lam": ("Lambdadelta", "f"), "mar": ("Maria", "f"), "na2": ("NanjoMasayuki", "m"), "nan": ("Nanjo", "m"),
    "nat": ("Natsuhi", "f"), "oko": ("Okonogi", "m"), "pro": ("Ootsuki", "m"),
    "rg1": ("Lucifer", "f"), "rg2": ("Leviathan", "f"), "rg3": ("Satan", "f"), "rg4": ("Belphegor", "f"),
    "rg5": ("Mammon", "f"), "rg6": ("Beelzebub", "f"), "rg7": ("Asmodeus", "f"),
    "rio": ("Lion", None), "ron": ("Ronove", "m"), "ros": ("Rosa", "f"), "rud": ("Rudolf", "m"),
    "s00": ("Chiester00", "f"), "s41": ("Chiester410", "f"), "s45": ("Chiester45", "f"),
    "s55": ("Chiester55", "f"), "s56": ("Chiester556", "f"), "sak": ("Sakutaro", "m"), "sha": ("Shannon", "f"),
    "wal": ("Virgilia", "f"), "wil": ("Willard", "m"), "zep": ("Zepar", None),
}
GENDER = {"m": "male", "f": "female"}

LV_RE = re.compile(r'(?:\[lv \d+\*"(\w+)"\*"[^"]*"\])+$')
MSGWND_RE = re.compile(r"^[ \t]*msgwnd_(\w+)", re.M)
TAG_OPEN_RE = re.compile(r'^(?:\{[a-z]+(?::[^:{}]*)?:|\{n\}|\s)+')


def base_id(rid):
    return rid.split(SEP, 1)[0]


def label(code, gender_on):
    name, g = CHARS.get(code, (code, None))
    return f"{name}_{GENDER[g]}" if gender_on and g else name


# ---- speaker detection from the game script ------------------------------------
def scan_game(ep, name):
    """Return {line_number: (msgwnd_code or None, voice_id or None)} or None if files are missing."""
    jp = os.path.join(STORY, ep, "jp", name + ".txt")
    game = os.path.join(GAME, "omake" if ep == "omake" else "main", name + ".txt")
    if not (os.path.exists(jp) and os.path.exists(game)):
        return None
    text = open(game, encoding="utf-8").read().replace("\r\n", "\n")
    marks = [(m.start(), m.group(1)) for m in MSGWND_RE.finditer(text)]
    pos = [p for p, _ in marks]
    out = {}
    # Same order and first-match rule as update-manager.php inplaceLines(): each
    # Japanese line replaces its first not-yet-used occurrence in the game script.
    for n, line in enumerate(open(jp, encoding="utf-8").read().replace("\r\n", "\n").split("\n"), 1):
        s = line.strip()
        if not s:
            continue
        p = text.find(s)
        if p < 0:
            continue
        text = text[:p] + "\0" * len(s) + text[p + len(s):]
        k = bisect.bisect_right(pos, p) - 1
        m = LV_RE.search(text[max(0, p - 200):p])
        out[n] = (marks[k][1] if k >= 0 else None, m.group(1) if m else None)
    return out


_voice_cache = {}
MOB_VOICES = {"00", "99"}      # generic / extra voices, used for many different people


def voice_gender():
    """voice id -> 'm'/'f' learned from the named lines that use the voice (all game files).
    Only kept when every character that shares the voice has the same gender."""
    if _voice_cache:
        return _voice_cache
    counts = defaultdict(Counter)
    for d in ("main", "omake"):
        for f in glob.glob(os.path.join(GAME, d, "*.txt")):
            code = None
            for line in open(f, encoding="utf-8"):
                m = MSGWND_RE.match(line)
                if m:
                    code = m.group(1); continue
                if code and code != "non":
                    for v in re.findall(r'\[lv \d+\*"(\w+)"\*', line):
                        counts[v][code] += 1
    for v, c in counts.items():
        if v in MOB_VOICES:
            continue
        total = sum(c.values())
        genders = {CHARS.get(code, ("", None))[1] for code, k in c.items() if k >= 0.05 * total}
        if len(genders) == 1 and None not in genders:
            _voice_cache[v] = genders.pop()
    return _voice_cache


def quote_kind(rows):
    """Fallback without jp/game files: dialogue/narration from the English quotes."""
    out, inq = {}, False
    for rid, src in rows:
        core = TAG_OPEN_RE.sub("", src.strip()).rstrip("}").rstrip()
        if core.startswith('"'):
            inq = True
        out[rid] = "dialogue" if inq else "narration"
        if core.endswith('"') and (len(core) > 1 or not core.startswith('"')):
            inq = False
    return out


def tags_for(ep, name, rows, gender_on):
    info = scan_game(ep, name)
    fallback = quote_kind(rows)
    vgen = voice_gender() if info is not None else {}
    tags, stats = {}, Counter()
    for rid, src in rows:
        n = int(rid.rsplit(":", 1)[1])
        hit = info.get(n) if info is not None else None
        if hit is None:
            tags[rid] = fallback[rid]; stats["fallback"] += 1; continue
        code, voice = hit
        if code and code != "non":
            tags[rid] = label(code, gender_on); stats["speaker"] += 1
        elif voice:
            g = vgen.get(voice)
            tags[rid] = "unnamed_" + GENDER[g] if gender_on and g else "unnamed"; stats["unnamed"] += 1
        else:
            tags[rid] = "narration"; stats["narration"] += 1
    return tags, stats


# ---- CSV --------------------------------------------------------------------------
def process(csv_path, args):
    base = csv_path[:-4]
    name = os.path.basename(base)
    ep = os.path.basename(os.path.dirname(csv_path))
    if os.path.exists(base + "_translated.csv"):
        return name, None, "skipped: " + name + "_translated.csv exists (IDs must stay as TStudio saw them)"
    raw = open(csv_path, "rb").read()
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    header, body = rows[0], rows[1:]
    if header[:1] != ["ID"]:
        return name, None, "skipped: not an ID/Source CSV"
    plain = [(base_id(r[0]), r[1]) for r in body]
    if args.strip:
        tags, stats = {rid: None for rid, _ in plain}, Counter(stripped=len(plain))
    else:
        tags, stats = tags_for(ep, name, plain, not args.no_gender)
    for r, (rid, _) in zip(body, plain):
        r[0] = rid + (SEP + tags[rid] if tags[rid] else "")
    buf = io.StringIO(newline="")
    w = csv.writer(buf)            # same writer settings as story2csv.py
    w.writerow(header)
    w.writerows(body)
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    changed = data != raw
    if changed and not args.check:
        tmp = csv_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, csv_path)
    stats["changed_files"] += int(changed)
    return name, stats, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", nargs="*", help="ep1 ... ep8, omake (default: all)")
    ap.add_argument("--only", nargs="*", help="file names without .csv, e.g. umi1_op")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    ap.add_argument("--strip", action="store_true", help="remove the @speaker tags")
    ap.add_argument("--no-gender", action="store_true", help="do not add _male/_female")
    a = ap.parse_args()
    eps = a.episodes or sorted(d for d in os.listdir(CSV_DIR)
                               if d != "script" and os.path.isdir(os.path.join(CSV_DIR, d)))
    total = Counter()
    for ep in eps:
        files = sorted(f for f in glob.glob(os.path.join(CSV_DIR, ep, "*.csv"))
                       if not f.endswith("_translated.csv") and os.path.exists(f[:-4] + ".umimeta.json"))
        if not files:
            print(f"!! {ep}: no story CSVs found"); continue
        ep_stats = Counter()
        for f in files:
            nm = os.path.basename(f)[:-4]
            if a.only and nm not in a.only:
                continue
            name, st, msg = process(f, a)
            if msg:
                print(f"   {ep}/{name}: {msg}"); continue
            ep_stats.update(st)
        total.update(ep_stats)
        print(f"{ep}: " + ", ".join(f"{k} {v}" for k, v in sorted(ep_stats.items())))
    print("TOTAL: " + ", ".join(f"{k} {v}" for k, v in sorted(total.items())))
    print("check only, nothing written" if a.check else "done")


if __name__ == "__main__":
    sys.exit(main())
