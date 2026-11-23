"""Split content-bearing tags so TStudio can translate the text inside them.

TStudio (TRun/trun_app.py:161) treats  {anything up to the first "}"}  as ONE
untouchable tag and refuses any change inside it, so the words inside
{p:1:red truth}, {i:..}, {c:..:..}, {f:5:Name} ... were never translated.
In the CSV Source column those tags are therefore written as an opener token,
the text, and a closer token - every token is short and has no text inside:

    {p:1:I acknowledge it.}          ->  {p:1}I acknowledge it.{/p}
    {i:she had}                      ->  {i}she had{/i}
    Kinzo-{c:86EF9C:san}             ->  Kinzo-{c:86EF9C}san{/c}
    {p:1:{f:5:Beatrice} is dead}     ->  {p:1}{f:5}Beatrice{/f} is dead{/p}
    {ruby:Gospel:Fukuin}             ->  {ruby}Gospel{rb}Fukuin{/ruby}  (reading {rb} base text)
    <Good>                           ->  {en}Good{/en}            (English-spoken words)

Kept as ONE token (nothing to translate inside): tags without letters
({p:4:嘉音}, {p:0:♪}, {m:-5:—}, {c:ff0000:♥♥}), empty tags ({w:700:}, {a:c:}),
simple tags ({n}, {qt}, {0}, {-}) and {Comment:..}.

A tag opened on one line and closed on a later line gives an opener without a
closer on the first line and a closer without an opener on the later line.

join() turns the split form back into the original syntax; it is purely
token-by-token, so join(split(x)) == x and join(original) == original.
"""
import re
from collections import Counter

NOPARAM = {"i", "nobr", "nobreak", "b"}          # {i:text}
ATOMIC = {"Comment"}                              # never split
TOKEN_RE = re.compile(r"\{[^{}]*\}")
LETTER_RE = re.compile(r"[A-Za-z]")
NAME_RE = re.compile(r"[^{}:/]*")
OPEN_PARAM_RE = re.compile(r"^\{([A-Za-z]+)(?:(:)([^{}:]*)|(/)([^{}/]*))\}$")
OPEN_NOPARAM_RE = re.compile(r"^\{(%s)\}$" % "|".join(sorted(NOPARAM)))
CLOSE_RE = re.compile(r"^\{/([A-Za-z]*)\}$")
EN_OPEN, EN_CLOSE = "{en}", "{/en}"
RUBY_OPEN, RUBY_SEP = "{ruby}", "{rb}"            # {ruby:reading:base}


def _match_close(s, i):
    """index of the "}" closing the "{" at s[i], or -1 if the line ends first."""
    depth = 0
    for k in range(i, len(s)):
        if s[k] == "{":
            depth += 1
        elif s[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    return -1


def _text(seg):
    return seg.replace("<", EN_OPEN).replace(">", EN_CLOSE)


def split(s, ctx=None):
    """Return the split form of s.  ctx: list of tag names still open from earlier
    lines of the same file (updated in place) - used to name a stray closer."""
    if ctx is None:
        ctx = []
    out, i, n, buf = [], 0, len(s), ""
    while i < n:
        ch = s[i]
        if ch == "}":
            out.append(_text(buf)); buf = ""
            out.append("{/%s}" % (ctx.pop() if ctx else ""))
            i += 1; continue
        if ch != "{":
            buf += ch; i += 1; continue
        out.append(_text(buf)); buf = ""
        m = NAME_RE.match(s, i + 1)
        name, j = m.group(0), m.end()
        nxt = s[j] if j < n else ""
        end = _match_close(s, i)
        if nxt == "}" or nxt not in (":", "/") or name in ATOMIC or not name:
            if end < 0:                         # cannot happen in this game; keep literally
                out.append(s[i:]); i = n; continue
            out.append(s[i:end + 1]); i = end + 1; continue
        if name in NOPARAM:
            opener, cstart = "{%s}" % name, j + 1
        else:
            k = j + 1
            while k < n and s[k] not in "{}" + nxt:
                k += 1
            if k >= n or s[k] != nxt:           # {x:y} with one separator only -> keep whole
                if end < 0:
                    out.append(s[i:]); i = n; continue
                out.append(s[i:end + 1]); i = end + 1; continue
            opener, cstart = "{%s%s%s}" % (name, nxt, s[j + 1:k]), k + 1
            if name == "ruby" and nxt == ":":   # {ruby:reading:base} -> {ruby}reading{rb}base{/ruby}
                whole = s[j + 1:end] if end >= 0 else s[j + 1:]
                if end >= 0 and "{" not in whole and not LETTER_RE.search(whole):
                    out.append(s[i:end + 1]); i = end + 1; continue
                opener = "{ruby}" + split(s[j + 1:k], []) + RUBY_SEP
        content = s[cstart:end] if end >= 0 else s[cstart:]
        if end >= 0 and name not in NOPARAM and name != "ruby" and "{" not in content and not LETTER_RE.search(content):
            out.append(s[i:end + 1]); i = end + 1; continue      # nothing to translate inside
        out.append(opener)
        if end < 0:                             # closes on a later line
            out.append(split(content, []))
            ctx.append(name); i = n; continue
        out.append(split(content, []))
        out.append("{/%s}" % name)
        i = end + 1
    out.append(_text(buf))
    return "".join(out)


def _join_token(tok):
    if tok == EN_OPEN:
        return "<"
    if tok == EN_CLOSE:
        return ">"
    if CLOSE_RE.match(tok):
        return "}"
    if tok == RUBY_OPEN:
        return "{ruby:"
    if tok == RUBY_SEP:
        return ":"
    m = OPEN_PARAM_RE.match(tok)
    if m and m.group(1) not in NOPARAM and m.group(1) not in ATOMIC:
        sep, param = (m.group(2), m.group(3)) if m.group(2) else (m.group(4), m.group(5))
        return "{%s%s%s%s" % (m.group(1), sep, param, sep)
    m = OPEN_NOPARAM_RE.match(tok)
    if m:
        return "{%s:" % m.group(1)
    return tok


def join(s):
    """Split form -> original tag syntax (identity on text that was never split)."""
    return TOKEN_RE.sub(lambda m: _join_token(m.group(0)), s)


def is_split_form(s):
    return "{" not in TOKEN_RE.sub("", s) and "}" not in TOKEN_RE.sub("", s)


_CLOSER_ANY = re.compile(r"\{/[A-Za-z]*\}")


def is_split_source(s):
    """True if s is exactly what split() makes (a CSV written by split_tags/story2csv);
    False for the old {p:1:text} form."""
    if not is_split_form(s):
        return False
    norm = lambda x: _CLOSER_ANY.sub("{/}", x)
    return norm(split(join(s), [])) == norm(s)


def _kind(tok):
    if tok in (EN_OPEN, EN_CLOSE):
        return "en", None
    if tok == RUBY_OPEN:
        return "open", "ruby"
    if tok == RUBY_SEP:
        return "sep", "ruby"
    m = CLOSE_RE.match(tok)
    if m:
        return "close", m.group(1)
    m = OPEN_PARAM_RE.match(tok)
    if m and m.group(1) not in NOPARAM and m.group(1) not in ATOMIC:
        return "open", m.group(1)
    if OPEN_NOPARAM_RE.match(tok):
        return "open", tok[1:-1]
    return "atom", None


def structure(s):
    """(Counter of tokens, en sequence, stray closers, unclosed openers) or None if
    the tags do not nest properly."""
    toks = TOKEN_RE.findall(s)
    stack, stray, ens = [], [], []
    for t in toks:
        k, name = _kind(t)
        if k == "en":
            ens.append(t)
        elif k == "open":
            stack.append(name)
        elif k == "sep":                    # {rb} must sit directly inside {ruby}..{/ruby}
            if not stack or stack[-1] != "ruby":
                return None
            stack[-1] = "ruby|"             # ... and only once
            continue
        elif k == "close":
            if stack:
                if stack[-1] == "ruby":     # {ruby} without {rb}
                    return None
                if name and stack[-1].rstrip("|") != name:
                    return None
                stack.pop()
            else:
                stray.append(name)
    return Counter(toks), ens, stray, stack


def tags_ok(src, tr):
    """True if the translation keeps the tags of the source.

    Split form: the translation must contain exactly the same tokens (same
    number of each), every closer must close the opener right before it
    (proper nesting), the lines' open/close carry-over must be the same, and
    {en}/{/en} must come in the same order.  The position of a tag pair inside
    the sentence may move (Thai word order).  Unsplit (old) CSVs fall back to the
    old rule: same tag names in the same order and the same number of braces."""
    if not is_split_source(src):
        return old_shape(src) == old_shape(tr)
    if not is_split_form(tr):
        return False
    a, b = structure(src), structure(tr)
    return a is not None and b is not None and a == b


_OLD_TAG_RE = re.compile(r"\{([a-z]+)(:)?")


def old_shape(s):
    return (_OLD_TAG_RE.findall(s), s.count("{"), s.count("}"))
