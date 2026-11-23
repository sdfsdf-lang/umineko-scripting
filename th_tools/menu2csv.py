"""Convert the Witch-Hunt menu/header scripts (script/wh/menu.txt, header.txt) to TStudio CSVs.

Only human-visible English text inside the NScripter string literals of the
"text" sections is extracted.  The sprite prefix (e.g. ":s;#C7C7C7#FFFFFF`")
and the wrapper tags around the whole text (e.g. "{p:13:{fit}{w:700:}" ... "}")
are kept out of the CSV, so the AI only sees the words plus inline tags such as
{n}, {i:..}, {f:5:..}, {p:42:..}.  Everything needed to rebuild the file
byte-for-byte is saved in <name>.umimeta.json.

Usage (from the repo root):
    python th_tools/menu2csv.py               # menu + header
    python th_tools/menu2csv.py menu          # only menu.txt
    python th_tools/menu2csv.py --force       # overwrite existing CSVs
    python th_tools/menu2csv.py --check       # write nothing, just verify round-trip

Output: th_work/csv/script/<name>.csv  (UTF-8 with BOM, columns ID,Source,Translation,AI_Reference)
        th_work/csv/script/<name>.umimeta.json
        th_work/csv/script/<name>.skipped.txt  (strings inside text sections that were NOT extracted)
Existing CSVs are skipped unless --force, so translations are never overwritten by accident.
"""
import argparse, csv, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tagsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "script", "wh")
OUT = os.path.join(ROOT, "th_work", "csv", "script")

# ---- which sections hold text -------------------------------------------------
# menu.txt: top-level ";section" headers. Sub-headers (;ep1, ;EP2_2, ;page 1 ...)
# inherit the section above them.  True = extract, False = leave alone.
MENU_SECTIONS = {
    "Tips": True, "Grimoire": True, "System button": False, "sub_menu": False,
    "cache": False, "main_menu": True, "Tips texts": True, "Grimoire texts": True,
    "scenario": True, "global": True, "chapter screen": False, "scenario menu": True,
    "menu setting": False, "Save load menu": True, "buttons": False, "session": True,
    "Captions": False, "Char texts": True, "bern quiz": True, "menu settings": True,
    "r_click_chapters": False, "r_click_buttons": False, "Character names": True,
    "menu answer ep8": True, "Bern game chapter hints": True, "trophies": True,
    "ugly temporary config fix": False, "quiz 8 episode": False, "last subs ep8": False,
}
# header.txt: ";xxx" comment headers that switch extraction on/off.
HEADER_SECTIONS = {
    "Game window caption": True, "--Textboxes--": False, "--Locale-end--": False,
    "--System-text--": True, "--System-text-end--": False,
}
FILES = {"menu": MENU_SECTIONS, "header": HEADER_SECTIONS}


def section_of(comment, table):
    c = comment.strip()
    for name in table:                       # exact, or "Grimoire texts ep1" -> "Grimoire texts"
        if c == name or (c.startswith(name + " ") and name in ("Grimoire texts", "r_click_chapters", "r_click_buttons")):
            return name
    if c.startswith("r_click_chapters"): return "r_click_chapters"
    if c.startswith("r_click_buttons"): return "r_click_buttons"
    return None


# ---- splitting a line into code / string literals -------------------------------
def split_literals(line):
    """Return list of parts: ('code', text) or ('str', inner) ; stops at ; comment."""
    parts, buf, i, n = [], "", 0, len(line)
    while i < n:
        ch = line[i]
        if ch == '"':
            j = line.find('"', i + 1)
            if j < 0:                        # unterminated: treat rest as code
                buf += line[i:]; break
            parts.append(("code", buf + '"')); buf = ""
            parts.append(("str", line[i + 1:j]))
            buf = '"'; i = j + 1; continue
        if ch == ";":
            buf += line[i:]; break
        buf += ch; i += 1
    parts.append(("code", buf))
    return parts


SPRITE_RE = re.compile(r"^:[a-z][^`\\]*`")            # :s;#FFFFFF#FF0000`
EMPTY_TAG_RE = re.compile(r"^\{[a-z]+(?::[0-9A-Za-z#]{0,8})*:?\}")  # {fit} {w:700:} {a:c:}
WRAP_RE = re.compile(r"^\{(?:p|a|g|f|c):[0-9A-Za-z#]{1,8}:")  # {p:13:  {a:c:  {f:5:


def match_close(s, start):
    depth = 0
    for k in range(start, len(s)):
        if s[k] == "{": depth += 1
        elif s[k] == "}":
            depth -= 1
            if depth == 0: return k
    return -1


def peel(lit):
    """Split literal into (pre, body, post) so pre+body+post == lit."""
    pre, body, post = "", lit, ""
    m = SPRITE_RE.match(body)
    if m:
        pre, body = m.group(0), body[m.end():]
    if body.endswith("`"):
        body, post = body[:-1], "`"
    while True:
        m = EMPTY_TAG_RE.match(body)
        if m and m.end() < len(body):
            pre += m.group(0); body = body[m.end():]; continue
        m = WRAP_RE.match(body)
        if m and match_close(body, 0) == len(body) - 1 and len(body) > m.end() + 1:
            pre += m.group(0); post = "}" + post; body = body[m.end():-1]; continue
        break
    return pre, body, post


PATH_RE = re.compile(r"\\|\.(png|jpe?g|bmp|ass|ogg|wav|mp3|mp4|webm|txt|ttf|otf)\b", re.I)
TAGTEXT_RE = re.compile(r"\{[a-z]+:(?:[0-9A-Za-z#]{1,8}:)?|[{}]")


def classify(body):
    """Return None if body is player-visible text, else a skip reason."""
    if not body.strip():
        return "empty"
    if PATH_RE.search(body) or ("/" in body and " " not in body):
        return "path/asset"
    vis = TAGTEXT_RE.sub(" ", body)
    if re.fullmatch(r"\s*#?[0-9A-Fa-f]{6}\s*", vis) or re.fullmatch(r"[\s\d.,:+\-]*", vis):
        return "number/colour"
    if not re.search(r"[A-Za-z]{2,}", vis):
        return "no English word"
    if " " not in vis.strip() and re.fullmatch(r"[a-z_]*\d[A-Za-z0-9_]*|[a-z0-9]*_[a-z0-9_]*", vis.strip()):
        return "identifier"
    return None


def scan(name, text_lines, table):
    """Yield per-line meta + rows + skipped."""
    rows, meta_lines, skipped = [], [], []
    current = None
    for i, line in enumerate(text_lines, 1):
        if line.lstrip().startswith(";"):
            sec = section_of(line.lstrip()[1:], table)
            if sec is not None:
                current = sec
            meta_lines.append({"raw": line}); continue
        active = bool(current and table.get(current))
        parts = split_literals(line)
        if not active or not any(p[0] == "str" for p in parts):
            meta_lines.append({"raw": line}); continue
        out_parts, k, got = [], 0, False
        for kind, val in parts:
            if kind == "code":
                out_parts.append(val); continue
            pre, body, post = peel(val)
            reason = classify(body)
            if reason:
                out_parts.append(val)
                if reason != "empty":
                    skipped.append(f"{i:04d}:{k}  [{reason}]  \"{val}\"  ({current})")
            else:
                rid = f"script/{name}:{i:04d}:{k}"
                rows.append([rid, tagsplit.split(body), "", ""])   # see tagsplit.py
                out_parts.append({"id": rid, "pre": pre, "post": post}); got = True
            k += 1
        meta_lines.append({"parts": out_parts} if got else {"raw": line})
    return rows, meta_lines, skipped


def rebuild(meta, texts):
    out = []
    for l in meta["lines"]:
        if "raw" in l:
            out.append(l["raw"]); continue
        s = ""
        for p in l["parts"]:
            s += p if isinstance(p, str) else p["pre"] + texts[p["id"]] + p["post"]
        out.append(s)
    return meta["newline"].join(out) + (meta["newline"] if meta["final_newline"] else "")


def convert(name, force, check):
    path = os.path.join(SCRIPT, name + ".txt")
    csv_path = os.path.join(OUT, name + ".csv")
    meta_path = os.path.join(OUT, name + ".umimeta.json")
    if os.path.exists(csv_path) and not force and not check:
        print(f"   script/{name}: CSV exists, skipped (use --force)"); return
    raw = open(path, "rb").read()
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    final_newline = bool(text) and lines[-1] == ""
    if final_newline:
        lines = lines[:-1]
    rows, meta_lines, skipped = scan(name, lines, FILES[name])
    meta = {"source": os.path.relpath(path, ROOT).replace("\\", "/"), "newline": newline,
            "final_newline": final_newline, "bom": False, "lines": meta_lines}
    ok = rebuild(meta, {r[0]: tagsplit.join(r[1]) for r in rows}).encode("utf-8") == raw
    print(f"script/{name}: {len(rows)} strings, {len(skipped)} skipped in text sections, round-trip {'OK' if ok else 'FAILED'}")
    if not ok:
        return 1
    if check:
        return
    os.makedirs(OUT, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Source", "Translation", "AI_Reference"])
        w.writerows(rows)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    with open(os.path.join(OUT, name + ".skipped.txt"), "w", encoding="utf-8") as f:
        f.write(f"Strings inside text sections of script/wh/{name}.txt that were NOT put in the CSV\n")
        f.write("line:k  [reason]  literal  (section)\n\n" + "\n".join(skipped) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="menu, header (default: both)")
    ap.add_argument("--force", action="store_true", help="overwrite existing CSVs")
    ap.add_argument("--check", action="store_true", help="write nothing, only verify round-trip")
    a = ap.parse_args()
    bad = 0
    for n in a.names or ["menu", "header"]:
        if n not in FILES:
            print(f"!! unknown file {n}"); continue
        bad |= bool(convert(n, a.force, a.check))
    print("check only, nothing written" if a.check else f"Done -> {OUT}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
