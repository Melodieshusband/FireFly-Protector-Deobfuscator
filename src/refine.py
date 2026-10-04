import re

STR = re.compile(r'"(?:[^"\\]|\\.)*"')
MAKE = re.compile(r"make\[(\d+)\]\(\{([^{}]*)\}\)")


def mask(text):
    items = []

    def f(m):
        items.append(m.group(0))
        return '"\x00%d\x00"' % (len(items) - 1)

    return STR.sub(f, text), items


def unmask(text, items):
    return re.sub(r'"\x00(\d+)\x00"', lambda m: items[int(m.group(1))], text)


def split_top(s):
    out = []
    depth = 0
    cur = []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    last = "".join(cur).strip()
    if last or out:
        out.append(last)
    return out


class Namer:
    def __init__(self):
        self.count = {"r": 0, "l": 0, "k": 0}

    def fresh(self, kind):
        self.count[kind] += 1
        return "%s%d" % (kind, self.count[kind])


def rename(text, namer):
    masked, items = mask(text)
    maps = {}

    def sub(m):
        tok = m.group(0)
        if tok in maps:
            return maps[tok]
        if tok.startswith("r_"):
            kind = "r"
        elif tok.startswith("c_"):
            kind = "k"
        else:
            kind = "l"
        maps[tok] = namer.fresh(kind)
        return maps[tok]

    masked = re.sub(r"\b(?:r_\w+|c_\d+|t\d+_\d+)\b", sub, masked)
    return unmask(masked, items)


def indent_block(text, pad):
    return "\n".join((pad + ln if ln.strip() else ln) for ln in text.split("\n"))


class Inliner:
    def __init__(self, bodies, arity):
        self.bodies = bodies
        self.arity = arity
        self.used = set()

    def cell_names(self, masked):
        cells = set(re.findall(r"\blocal (l\d+) = \{\}", masked))
        cells |= set(re.findall(r"^\s*(l\d+) = \{\}", masked, re.M))
        return cells

    def expand(self, text, stack=()):
        while True:
            masked, items = mask(text)
            cells = self.cell_names(masked)
            done = False
            for m in MAKE.finditer(masked):
                key = int(m.group(1))
                if key in stack or key not in self.bodies:
                    continue
                caps = split_top(m.group(2))
                ok = True
                for c in caps:
                    if re.fullmatch(r"up\[\d+\]", c):
                        continue
                    if re.fullmatch(r"l\d+", c) and c in cells:
                        continue
                    ok = False
                    break
                if not ok:
                    continue
                body = self.bodies[key]
                bm, bitems = mask(body)

                def capsub(mm, caps=caps):
                    i = int(mm.group(1))
                    return caps[i - 1] if i - 1 < len(caps) else "nil"

                bm = re.sub(r"\bup\[(\d+)\]", capsub, bm)
                ar = self.arity.get(key)
                params = "..."
                if isinstance(ar, int):
                    names = ["a%d" % (i + 1) for i in range(ar)]
                    for i in range(ar):
                        bm = bm.replace("(select(%d, ...))" % (i + 1), names[i])
                    uses_va = "..." in bm
                    keep = 0
                    for i in range(ar):
                        if re.search(r"\\b%s\\b" % names[i], bm):
                            keep = i + 1
                    names = names[:keep]
                    params = ", ".join(names + (["..."] if uses_va else []))
                nested = unmask(bm, bitems)
                nested = self.expand(nested, stack + (key,))
                line_start = masked.rfind("\n", 0, m.start()) + 1
                pad = re.match(r"\s*", masked[line_start:]).group(0)
                rep = "function(%s)\n%s\n%send" % (params, indent_block(nested, pad + "    "), pad)
                rep_masked, rep_items = mask(rep)
                new_masked = masked[:m.start()] + rep + masked[m.end():]
                text = unmask(new_masked, items)
                done = True
                break
            if not done:
                return text


def promote_cells(text):
    masked, items = mask(text)
    cells = re.findall(r"\blocal (l\d+) = \{\}", masked)
    for c in cells:
        if len(re.findall(r"\blocal %s = \{\}" % c, masked)) != 1:
            continue
        others = [m for m in re.finditer(r"\b%s\b(?!\.v\b)" % c, masked)]
        decl = re.search(r"\blocal %s = \{\}" % c, masked)
        bad = [m for m in others if not (decl.start() <= m.start() < decl.end())]
        if bad:
            continue
        masked = re.sub(r"\blocal %s = \{\}" % c, "local %s" % c, masked)
        masked = re.sub(r"\b%s\.v\b" % c, c, masked)
    return unmask(masked, items)


def hoisted_cells(text):
    return text
