CAP_UNFINISHED = -1
CAP_POSITION = -2
MAX_DEPTH = 200
SPECIALS = set("^$*+?.([%-")

UPPER = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
LOWER = set("abcdefghijklmnopqrstuvwxyz")
DIGIT = set("0123456789")
ALPHA = UPPER | LOWER
ALNUM = ALPHA | DIGIT
XDIGIT = DIGIT | set("abcdefABCDEF")
SPACE = set(" \t\n\r\f\v")
PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
CNTRL = set(chr(i) for i in range(32)) | {chr(127)}

CLASSES = {
    "a": ALPHA,
    "c": CNTRL,
    "d": DIGIT,
    "l": LOWER,
    "p": PUNCT,
    "s": SPACE,
    "u": UPPER,
    "w": ALNUM,
    "x": XDIGIT,
}


class PatternError(Exception):
    pass


def match_class(c, cl):
    low = cl.lower()
    cset = CLASSES.get(low)
    if cset is None:
        return cl == c
    res = c in cset
    if cl in UPPER:
        return not res
    return res


class MatchState:
    __slots__ = ("src", "pat", "capture", "depth")

    def __init__(self, src, pat):
        self.src = src
        self.pat = pat
        self.capture = []
        self.depth = MAX_DEPTH

    def reset(self):
        self.capture = []
        self.depth = MAX_DEPTH


def class_end(ms, p):
    pat = ms.pat
    plen = len(pat)
    if p >= plen:
        raise PatternError("malformed pattern (ends with '%')")
    c = pat[p]
    p += 1
    if c == "%":
        if p >= plen:
            raise PatternError("malformed pattern (ends with '%')")
        return p + 1
    if c == "[":
        if p < plen and pat[p] == "^":
            p += 1
        while True:
            if p >= plen:
                raise PatternError("malformed pattern (missing ']')")
            cc = pat[p]
            p += 1
            if cc == "%" and p < plen:
                p += 1
            if p < plen and pat[p] == "]":
                break
            if p >= plen:
                raise PatternError("malformed pattern (missing ']')")
        return p + 1
    return p


def match_bracket_class(ms, c, p, ec):
    pat = ms.pat
    sig = True
    if pat[p + 1] == "^":
        sig = False
        p += 1
    p += 1
    while p < ec:
        ch = pat[p]
        if ch == "%":
            p += 1
            if match_class(c, pat[p]):
                return sig
        elif pat[p + 1] == "-" and p + 2 < ec:
            if pat[p] <= c <= pat[p + 2]:
                return sig
            p += 2
        elif ch == c:
            return sig
        p += 1
    return not sig


def single_match(ms, s, p, ep):
    if s >= len(ms.src):
        return False
    c = ms.src[s]
    pc = ms.pat[p]
    if pc == ".":
        return True
    if pc == "%":
        return match_class(c, ms.pat[p + 1])
    if pc == "[":
        return match_bracket_class(ms, c, p, ep - 1)
    return pc == c


def match_balance(ms, s, p):
    if p + 1 >= len(ms.pat):
        raise PatternError("unbalanced pattern")
    src = ms.src
    if s >= len(src) or src[s] != ms.pat[p]:
        return -1
    b = ms.pat[p]
    e = ms.pat[p + 1]
    cont = 1
    i = s + 1
    while i < len(src):
        c = src[i]
        if c == e:
            cont -= 1
            if cont == 0:
                return i + 1
        elif c == b:
            cont += 1
        i += 1
    return -1


def max_expand(ms, s, p, ep):
    i = 0
    while single_match(ms, s + i, p, ep):
        i += 1
    while i >= 0:
        res = do_match(ms, s + i, ep + 1)
        if res != -1:
            return res
        i -= 1
    return -1


def min_expand(ms, s, p, ep):
    while True:
        res = do_match(ms, s, ep + 1)
        if res != -1:
            return res
        if single_match(ms, s, p, ep):
            s += 1
        else:
            return -1


def start_capture(ms, s, p, what):
    ms.capture.append([s, what])
    res = do_match(ms, s, p)
    if res == -1:
        ms.capture.pop()
    return res


def capture_to_close(ms):
    level = len(ms.capture) - 1
    while level >= 0:
        if ms.capture[level][1] == CAP_UNFINISHED:
            return level
        level -= 1
    raise PatternError("invalid pattern capture")


def end_capture(ms, s, p):
    l = capture_to_close(ms)
    ms.capture[l][1] = s - ms.capture[l][0]
    res = do_match(ms, s, p)
    if res == -1:
        ms.capture[l][1] = CAP_UNFINISHED
    return res


def check_capture(ms, ch):
    l = ord(ch) - ord("1")
    if l < 0 or l >= len(ms.capture) or ms.capture[l][1] == CAP_UNFINISHED:
        raise PatternError("invalid capture index")
    return l


def match_capture(ms, s, ch):
    l = check_capture(ms, ch)
    start, ln = ms.capture[l]
    if ln < 0:
        return -1
    if len(ms.src) - s >= ln and ms.src[start:start + ln] == ms.src[s:s + ln]:
        return s + ln
    return -1


def do_match(ms, s, p):
    ms.depth -= 1
    if ms.depth == 0:
        raise PatternError("pattern too complex")
    try:
        pat = ms.pat
        src = ms.src
        plen = len(pat)
        while True:
            if p == plen:
                return s
            pc = pat[p]
            if pc == "(":
                if p + 1 < plen and pat[p + 1] == ")":
                    return start_capture(ms, s, p + 2, CAP_POSITION)
                return start_capture(ms, s, p + 1, CAP_UNFINISHED)
            if pc == ")":
                return end_capture(ms, s, p + 1)
            if pc == "$" and p + 1 == plen:
                return s if s == len(src) else -1
            if pc == "%" and p + 1 < plen:
                nc = pat[p + 1]
                if nc == "b":
                    s = match_balance(ms, s, p + 2)
                    if s != -1:
                        p += 4
                        continue
                    return -1
                if nc == "f":
                    p += 2
                    if p >= plen or pat[p] != "[":
                        raise PatternError("missing '[' after '%f' in pattern")
                    ep = class_end(ms, p)
                    prev = src[s - 1] if s > 0 else "\0"
                    cur = src[s] if s < len(src) else "\0"
                    if (not match_bracket_class(ms, prev, p, ep - 1)) and match_bracket_class(ms, cur, p, ep - 1):
                        p = ep
                        continue
                    return -1
                if nc in DIGIT:
                    s = match_capture(ms, s, nc)
                    if s != -1:
                        p += 2
                        continue
                    return -1
            ep = class_end(ms, p)
            epc = pat[ep] if ep < plen else ""
            if epc == "?":
                if single_match(ms, s, p, ep):
                    res = do_match(ms, s + 1, ep + 1)
                    if res != -1:
                        return res
                p = ep + 1
                continue
            if epc == "+":
                if single_match(ms, s, p, ep):
                    return max_expand(ms, s + 1, p, ep)
                return -1
            if epc == "*":
                return max_expand(ms, s, p, ep)
            if epc == "-":
                return min_expand(ms, s, p, ep)
            if not single_match(ms, s, p, ep):
                return -1
            s += 1
            p = ep
    finally:
        ms.depth += 1


def get_capture(ms, i, s, e):
    if i >= len(ms.capture):
        if i == 0:
            return ms.src[s:e]
        raise PatternError("invalid capture index")
    start, ln = ms.capture[i]
    if ln == CAP_UNFINISHED:
        raise PatternError("unfinished capture")
    if ln == CAP_POSITION:
        return start + 1
    return ms.src[start:start + ln]


def get_captures(ms, s, e, whole_if_none):
    n = len(ms.capture)
    if n == 0 and whole_if_none:
        n = 1
    return [get_capture(ms, i, s, e) for i in range(n)]


def has_specials(pat):
    for c in pat:
        if c in SPECIALS:
            return True
    return False
