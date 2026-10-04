import re
from .container import group_end
from .lexer import match_ends, tokenize


T_HEAD = re.compile(
    r"(\w+)=\(function\(\)local \w+,\w+,\w+,\w+=(\d+),(\d+),(\d+),(\d+) local \w+,\w+=(\d+),(\d+) local \w+=\d+ "
    r"local (\w+),(\w+),(\w+),(\w+),(\w+)=(\d+),(\d+),(\d+),(\d+),(\d+) "
    r"local \w+=\d+ local \w+=\d+ local \w+=\d+ local (\w+)=(\d+) local \w+=\"([^\"]+)\"\s*local \w+=(\d+) local \w+=(\d+) local \w+=(\d+)")


def extract_t_source(src):
    m = T_HEAD.search(src)
    if not m:
        raise ValueError("string decoder not found")
    open_pos = m.start() + len(m.group(1)) + 1
    return m, src[m.start():group_end(src, open_pos)]


def classify_ciphers(tsrc):
    toks = tokenize(tsrc)
    ep = match_ends(toks)
    funcs = {}
    for i, t in enumerate(toks):
        if t.kind == "kw" and t.val == "local" and toks[i + 1].val == "function" and toks[i + 3].val == "(":
            name = toks[i + 2].val
            close = ep[i + 1]
            body = tsrc[toks[i + 4].start:toks[close].end]
            funcs[name] = body
    kinds = {}
    for name, body in funcs.items():
        if re.search(r"%2(?!\d)", body):
            kinds[name] = "rot"
        elif re.search(r"=\(\(\w+\+\w+\)\+\w+\)%256", body):
            kinds[name] = "state"
        else:
            ks = re.search(r"local (\w+)=\(\(\(\w+\+\w+\)\+\w+\)\+\w+\*\w+\)%256", body)
            res = re.search(r"\w+\[\w+\]=\((\w+)-(\w+)\)%256", body)
            if ks and res:
                kinds[name] = "rev" if res.group(1) == ks.group(1) else "sub"
    mp = re.search(r"local (\w+)=\{((?:\[\d\]=\w+[,;])+\[\d\]=\w+)\}return function\((\w+)\)return \1\[\3\]or (\w+) end", tsrc)
    table = {}
    for item in re.finditer(r"\[(\d)\]=(\w+)", mp.group(2)):
        table[int(item.group(1))] = kinds.get(item.group(2), "sub")
    default = kinds.get(mp.group(4), "sub")
    return table, default


def lua_number(text):
    try:
        v = float(text)
    except ValueError:
        return 0
    if v != v or v in (float("inf"), float("-inf")):
        return v
    return int(v) if v == int(v) else v


def parse_table_body(body):
    data = body.encode("latin-1")
    state = {"pos": 0, "fail": False}

    def byte():
        if state["pos"] >= len(data):
            state["fail"] = True
            return 0
        b = data[state["pos"]]
        state["pos"] += 1
        return b

    def u32():
        b0 = byte()
        b1 = byte()
        b2 = byte()
        b3 = byte()
        return b0 + b1 * 256 + b2 * 65536 + b3 * 16777216

    def text():
        n = u32()
        if state["fail"]:
            return ""
        if state["pos"] + n > len(data):
            state["fail"] = True
            return ""
        s = data[state["pos"]:state["pos"] + n].decode("latin-1")
        state["pos"] += n
        return s

    def value():
        tag = byte()
        if tag == 83:
            return ("const", text())
        if tag == 78:
            return ("const", lua_number(text()))
        if tag == 66:
            return ("const", byte() != 0)
        if tag == 90:
            return ("const", None)
        if tag == 84:
            count = u32()
            items = []
            for _ in range(count):
                if state["fail"]:
                    return None
                marker = byte()
                if marker == 65:
                    v = value()
                    if state["fail"]:
                        return None
                    items.append(("pos", v))
                elif marker == 75:
                    key = value()
                    v = value()
                    if state["fail"] or key is None or key == ("const", None):
                        state["fail"] = True
                        return None
                    items.append(("kv", key, v))
                else:
                    state["fail"] = True
                    return None
            return ("table", items)
        state["fail"] = True
        return None

    try:
        result = value()
    except RecursionError:
        return None
    if state["fail"] or state["pos"] < len(data) or result is None:
        return None
    if result[0] != "table":
        return None
    return result


class StringDecoder:
    def __init__(self, src):
        m, tsrc = extract_t_source(src)
        v = m.groups()
        self.ut, self.tt, self.qt, self.Ot = int(v[1]), int(v[2]), int(v[3]), int(v[4])
        self.zt, self.gt = int(v[5]), int(v[6])
        kvars = list(v[7:12])
        kvals = [int(x) for x in v[12:17]]
        kmap = {}
        mp = re.search(r"local \w+=\{((?:\[\d\]=\w+[,;])+\[\d\]=\w+)\}return function\(\w+\)local", tsrc)
        order = {}
        if mp:
            for item in re.finditer(r"\[(\d)\]=(\w+)", mp.group(1)):
                order[int(item.group(1))] = item.group(2)
        for mode, var in order.items():
            if var in kvars:
                kmap[mode] = kvals[kvars.index(var)]
        if not kmap:
            kmap = {i + 1: kvals[i] for i in range(5)}
        self.kinds = kmap
        self.lt = int(v[18])
        self.alphabet = v[19]
        self.mod = int(v[20])
        self.ma = int(v[21])
        self.mb = int(v[22])
        self.cipher_map, self.cipher_default = classify_ciphers(tsrc)
        self.alpha = {ord(c): i for i, c in enumerate(self.alphabet)}
        self.base = len(self.alphabet)

    def pairs(self, text):
        raw = text.encode("latin-1")
        out = []
        for i in range(0, len(raw), 2):
            c1 = self.alpha.get(raw[i])
            c2 = self.alpha.get(raw[i + 1]) if i + 1 < len(raw) else None
            if c1 is None or c2 is None:
                return None
            v = c1 * self.base + c2
            if v > 255:
                return None
            out.append(v)
        return out

    def cipher(self, kind, data, a, b):
        wt = self.qt % 256
        dt = self.Ot % 256
        M = self.mod
        u = (a + self.ut) % M
        t = (b + self.tt) % M
        if u == 0:
            u = 1
        if t == 0:
            t = 1
        out = []
        q = (a + b + wt) % 256
        for i, c in enumerate(data, 1):
            u = (u * self.ma) % M
            t = (t * self.mb) % M
            z = (u + t + wt + dt * i) % 256
            if kind == "state":
                x = (c - z - q) % 256
                out.append(x)
                q = (q + c + x + i) % 256
            elif kind == "rev":
                out.append((z - c) % 256)
            elif kind == "rot":
                g = (c - z) % 256
                x = g % 2
                out.append((g - x) // 2 + x * 128)
            else:
                out.append((c - z) % 256)
            u = (u + c) % M
        return out

    def decode(self, text, mode, nt, f5, t5, g5, m5, c5, a5=None, *extra):
        w5 = t5 if t5 is not None else f5
        data = self.pairs(text)
        if data is None:
            return None
        z = self.lt * 9973 + mode * 131
        ga = self.zt + nt + g5 + m5 + c5 + z
        xb = self.gt + f5 + g5 * 3 + m5 * 5 + c5 * 7 + z * 3
        sel = ((nt + mode * 3 + g5 * 5 + m5 * 7 + c5 * 11) % 4) + 1
        kind = self.cipher_map.get(sel, self.cipher_default)
        r = self.cipher(kind, data, ga, xb)
        if len(r) < 4:
            return None
        n = r[0] + r[1] * 256
        if n != len(r) - 4:
            return None
        acc = 0
        chars = []
        for i in range(1, n + 1):
            h = r[i + 3] % 256
            acc = (acc + h * (i % 251 + 1)) % 65536
            chars.append(h)
        if acc != r[2] + r[3] * 256:
            return None
        if w5 != f5:
            return None
        body = bytes(chars).decode("latin-1")
        k = self.kinds.get(mode)
        if k == 1:
            return body
        if k in (2, 5):
            return lua_number(body)
        if k == 3:
            return body == "1"
        if k == 4:
            return parse_table_body(body)
        return None
