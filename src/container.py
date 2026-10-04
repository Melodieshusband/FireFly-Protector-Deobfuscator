import re


MASK16 = 0xFFFF


def group_end(src, open_pos):
    depth = 0
    i = open_pos
    n = len(src)
    while i < n:
        c = src[i]
        if c == '"' or c == "'":
            j = i + 1
            while src[j] != c:
                if src[j] == "\\":
                    j += 1
                j += 1
            i = j
        elif c == "[" and src.startswith("[[", i):
            i = src.index("]]", i) + 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


WT_HEAD = re.compile(r"(\w+)=\(function\(\)local \w+,\w+,\w+,\w+=(\d+),(\d+),(\d+),(\d+) local \w+,\w+=(\d+),(\d+) local \w+=(\d+) local \w+=(\d+) local \w+=\"([^\"]+)\"\s*local \w+=(\d+)")


def extract_wt_source(src):
    m = WT_HEAD.search(src)
    if not m:
        raise ValueError("state loader not found")
    open_pos = m.start() + len(m.group(1)) + 1
    return src[m.start():group_end(src, open_pos)]


def parse_wt_params(wt_src):
    m = WT_HEAD.match(wt_src)
    if not m:
        raise ValueError("state loader header not recognised")
    v = m.groups()
    mod = re.search(r"local \w+=(2147483647) local \w+=(\d+) local \w+=(\d+) local function", wt_src)
    crc = re.search(r"\w+\(65535,(\d+)\)", wt_src)
    poly = re.search(r"\w+\(\w+\(\w+/2\),(\d+)\)", wt_src)
    return {
        "ut": int(v[1]), "tt": int(v[2]), "qt": int(v[3]), "Ot": int(v[4]),
        "zt": int(v[5]), "gt": int(v[6]), "rot": int(v[7]), "salt": int(v[8]),
        "alphabet": v[9], "total": int(v[10]),
        "mod": int(mod.group(1)) if mod else 2147483647,
        "ma": int(mod.group(2)) if mod else 48271,
        "mb": int(mod.group(3)) if mod else 69621,
        "crc_seed": int(crc.group(1)) if crc else 0,
        "crc_poly": int(poly.group(1)) if poly else 40961,
    }


def extract_chunks(src):
    m = re.search(r"\w+=\(function\(\)local \w+=\{\{\d+[,;]\d+[,;]\"", src)
    if not m:
        raise ValueError("chunk table not found")
    a = m.start()
    b = src.index("}}", a) + 2
    while True:
        tail = src[b:b + 12]
        if tail.startswith("local"):
            break
        b = src.index("}}", b) + 2
    seg = src[a:b]
    tag = re.search(r"if \w+\[1\]==(\d+) then", src[b:b + 600])
    out = []
    for mm in re.finditer(r"\{(\d+)[,;](\d+)[,;]\"([^\"]*)\"[,;](\d+)\}", seg):
        out.append((int(mm.group(1)), int(mm.group(2)), mm.group(3), int(mm.group(4))))
    return out, int(tag.group(1)) if tag else None


def verify_chunks(chunks):
    total = 0
    data = {}
    bad = 0
    for tag, ident, text, chk in chunks:
        x = (tag + ident * 17 + len(text) * 31) % 65536
        for i, ch in enumerate(text.encode("latin-1"), 1):
            x = (x + ch * (i % 13 + 1)) % 65536
        if x != chk:
            bad += 1
        total = (total + x) % 65536
        data.setdefault(tag, {})[ident] = text
    return data, total, bad


def b85_decode(parts, alphabet, total):
    table = {ord(c): i for i, c in enumerate(alphabet)}
    base = len(alphabet)
    out = []
    for text in parts:
        pos = 0
        raw = text.encode("latin-1")
        while pos + 4 < len(raw) + 0 and pos + 5 <= len(raw) and len(out) < total:
            v = 0
            for k in range(5):
                v = v * base + table[raw[pos + k]]
            for sh in (24, 16, 8, 0):
                if len(out) < total:
                    out.append((v >> sh) & 255)
            pos += 5
        if pos < len(raw):
            raise ValueError("trailing chars in chunk")
    if len(out) != total:
        raise ValueError("decoded length %d != %d" % (len(out), total))
    return out


def stream_decrypt(data, p):
    dt = p["mod"]
    ot = (p["zt"] + p["ut"]) % dt
    at = (p["gt"] + p["tt"]) % dt
    kt = p["qt"] % 256
    lt = p["Ot"] % 256
    if ot == 0:
        ot = 1
    if at == 0:
        at = 1
    out = []
    n = 0
    for b in data:
        n += 1
        ot = (ot * p["ma"]) % dt
        at = (at * p["mb"]) % dt
        it = (ot + at + kt + lt * n) % 256
        out.append((b - it) % 256)
        ot = (ot + b) % dt
    return out


def crc16(data, seed, poly):
    y = 65535 ^ seed
    for b in data:
        y ^= b
        for _ in range(8):
            if y & 1:
                y = (y >> 1) ^ poly
            else:
                y >>= 1
    return y


def lz_expand(src, want):
    out = []
    at = 0
    n = len(src)
    while at < n and len(out) < want:
        flags = src[at]
        at += 1
        for bit in range(8):
            if len(out) >= want or at >= n:
                break
            if (flags >> bit) & 1:
                dist = src[at]
                ln = src[at + 1] + 3
                at += 2
                for _ in range(ln):
                    out.append(out[len(out) - dist])
            else:
                out.append(src[at])
                at += 1
    return out


def le(buf, pos, width):
    v = 0
    for k in range(width):
        v += buf[pos + k] << (8 * k)
    return v


def unwrap_container(raw, p):
    if raw[0] == 76 and raw[1] == 2:
        flags = raw[2]
        want = le(raw, 3, 3)
        clen = le(raw, 6, 3)
        crc = le(raw, 9, 2)
        body = raw[11:11 + clen]
        if flags % 2 == 1:
            body = lz_expand(body, want)
        if len(body) != want:
            raise ValueError("container length mismatch")
        if crc16(body, p["crc_seed"], p["crc_poly"]) != crc:
            raise ValueError("container crc mismatch")
        return body
    return raw


def parse_state_table(buf, rot=0):
    if buf[0] != 76:
        raise ValueError("bad table magic")
    if buf[1] == 2:
        count = buf[2] + buf[3] * 256
        widths = buf[5]
        flags = buf[7] if len(buf) > 7 else 0
        pos = 8
    else:
        count = buf[1] + buf[2] * 256
        widths = buf[4]
        flags = 0
        pos = 6
    kw = widths // 16 + 1
    vw = widths % 16 + 1
    table = {}
    for _ in range(count):
        while pos < len(buf):
            kind = buf[pos]
            if kind == 255:
                break
            if kind == 0:
                pos += 2 + buf[pos + 1]
            elif kind == 1:
                pos += 1
                key = le(buf, pos, kw)
                pos += kw
                if rot > 0:
                    key = (key >> rot) + ((key % (1 << rot)) << (24 - rot))
                val = le(buf, pos, vw)
                pos += vw
                if flags % 2 == 1:
                    d2 = le(buf, pos, 2)
                    pos += 2
                    d3 = le(buf, pos, 2)
                    pos += 2
                    d4 = buf[pos]
                    pos += 1
                    d6 = buf[pos]
                    pos += 1
                    d5 = le(buf, pos, 2)
                    pos += 2
                    d7 = le(buf, pos, vw)
                    pos += vw
                    table[key] = [val, d2, d3, d4, d5, d6, d7]
                else:
                    table[key] = val
                break
            else:
                break
    return table


def load_state_table(src):
    p = parse_wt_params(extract_wt_source(src))
    chunks, tag = extract_chunks(src)
    data, total, bad = verify_chunks(chunks)
    if tag is None:
        tag = max(data, key=lambda k: len(data[k]))
    parts = [data[tag][k] for k in sorted(data[tag])]
    raw = b85_decode(parts, p["alphabet"], p["total"])
    plain = stream_decrypt(raw, p)
    body = unwrap_container(plain, p)
    p["chunk_mismatch"] = bad
    return parse_state_table(body, p["rot"]), p, total
