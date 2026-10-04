import re


KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}


NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


NUM = re.compile(r"0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?")


LONG_OPEN = re.compile(r"\[(=*)\[")


OPS3 = {"..."}


OPS2 = {"==", "~=", "<=", ">=", "..", "//", "::"}


class Tok:
    __slots__ = ("kind", "val", "start", "end")

    def __init__(self, kind, val, start, end):
        self.kind = kind
        self.val = val
        self.start = start
        self.end = end

    def __repr__(self):
        return "%s:%r" % (self.kind, self.val)


def unescape(body):
    out = []
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        c = body[i]
        simple = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\\": "\\", "\"": "\"", "'": "'", "\n": "\n"}
        if c in simple:
            out.append(simple[c])
            i += 1
        elif c.isdigit():
            j = i
            while j < n and j < i + 3 and body[j].isdigit():
                j += 1
            out.append(chr(int(body[i:j])))
            i = j
        elif c == "x":
            out.append(chr(int(body[i + 1:i + 3], 16)))
            i += 3
        elif c == "z":
            i += 1
            while i < n and body[i] in " \t\r\n":
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def tokenize(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if src.startswith("--", i):
            m = LONG_OPEN.match(src, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                i = n if j < 0 else j + len(close)
            else:
                j = src.find("\n", i)
                i = n if j < 0 else j
            continue
        if c.isalpha() or c == "_":
            m = NAME.match(src, i)
            w = m.group(0)
            toks.append(Tok("kw" if w in KEYWORDS else "name", w, i, m.end()))
            i = m.end()
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            m = NUM.match(src, i)
            toks.append(Tok("num", m.group(0), i, m.end()))
            i = m.end()
            continue
        if c in "\"'":
            j = i + 1
            while src[j] != c:
                if src[j] == "\\":
                    j += 1
                j += 1
            toks.append(Tok("str", unescape(src[i + 1:j]), i, j + 1))
            i = j + 1
            continue
        if c == "[":
            m = LONG_OPEN.match(src, i)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                body = src[m.end():j]
                if body.startswith("\n"):
                    body = body[1:]
                toks.append(Tok("str", body, i, j + len(close)))
                i = j + len(close)
                continue
        if src.startswith("...", i):
            toks.append(Tok("op", "...", i, i + 3))
            i += 3
            continue
        if src[i:i + 2] in OPS2:
            toks.append(Tok("op", src[i:i + 2], i, i + 2))
            i += 2
            continue
        toks.append(Tok("op", c, i, i + 1))
        i += 1
    return toks


OPENERS = {"function", "if", "do", "while", "for", "repeat"}


def match_ends(toks):
    pair = {}
    stack = []
    skip_do = 0
    for idx, t in enumerate(toks):
        if t.kind == "kw":
            v = t.val
            if v in ("while", "for"):
                stack.append((idx, v))
            elif v == "do":
                if stack and stack[-1][1] in ("while", "for") and stack[-1][0] >= 0 and not (len(stack[-1]) > 2):
                    stack[-1] = (stack[-1][0], stack[-1][1], True)
                else:
                    stack.append((idx, "do"))
            elif v in ("function", "if"):
                stack.append((idx, v))
            elif v == "repeat":
                stack.append((idx, v))
            elif v == "end":
                o = stack.pop()
                pair[o[0]] = idx
                pair[idx] = o[0]
            elif v == "until":
                o = stack.pop()
                pair[o[0]] = idx
                pair[idx] = o[0]
    return pair


def bracket_pairs(toks):
    pair = {}
    stack = []
    for idx, t in enumerate(toks):
        if t.kind == "op":
            if t.val in "({[":
                stack.append(idx)
            elif t.val in ")}]":
                o = stack.pop()
                pair[o] = idx
                pair[idx] = o
    return pair
