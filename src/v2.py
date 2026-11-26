import re
import struct

from .lexer import tokenize, match_ends, bracket_pairs

class V2Error(ValueError):
    pass


class Record:
    __slots__ = ("key", "block", "f2", "f3", "op_set", "check", "f6", "f7")

    def __init__(self, key, vals):
        self.key = key
        self.block, self.f2, self.f3, self.op_set, self.check, self.f6, self.f7 = vals


LUA_ESC = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, "\\": 92, '"': 34, "'": 39, "\n": 10}


def read_lua_string(src, pos):
    quote = src[pos]
    i = pos + 1
    out = bytearray()
    while src[i] != quote:
        c = src[i]
        if c == "\\":
            i += 1
            c = src[i]
            if c.isdigit():
                j = i
                while j < i + 3 and src[j].isdigit():
                    j += 1
                out.append(int(src[i:j]) & 255)
                i = j
                continue
            out.append(LUA_ESC.get(c, ord(c)))
            i += 1
            continue
        out.append(ord(c))
        i += 1
    return bytes(out), i + 1


def version_of(src):
    m = None
    for m in re.finditer(r"\bv\.\s*(\d+)\.(\d+)\.(\d+)", src[-4000:]):
        pass
    if m is None:
        return None
    return tuple(int(x) for x in m.groups())


def parse_chunk_table(src):
    m = re.search(r"=\(function\(\)local \w+=\{\{\d+[,;]\d+[,;]\"", src)
    if m is None:
        raise V2Error("chunk table not found")
    i = src.index("{{", m.start()) + 1
    chunks = []
    num_sep = re.compile(r"(\d+)\s*[,;]\s*(\d+)\s*[,;]\s*")
    tail = re.compile(r"\s*[,;]\s*(\d+)\s*\}")
    while True:
        while src[i] in " \t\r\n,;":
            i += 1
        if src[i] == "}":
            break
        if src[i] != "{":
            raise V2Error("malformed chunk table")
        i += 1
        mm = num_sep.match(src, i)
        a, idx = int(mm.group(1)), int(mm.group(2))
        i = mm.end()
        data, i = read_lua_string(src, i)
        mm = tail.match(src, i)
        chunks.append((a, idx, data, int(mm.group(1))))
        i = mm.end()
    return chunks


def chunk_checksum(a, idx, data):
    c = (a + idx * 17 + len(data) * 31) % 65536
    for k, b in enumerate(data, 1):
        c = (c + b * (k % 13 + 1)) % 65536
    return c


def base_decode(parts, alphabet, total):
    table = {ord(c): i for i, c in enumerate(alphabet)}
    base = len(alphabet)
    out = []
    for part in parts:
        pos = 0
        while pos + 4 < len(part) and len(out) < total:
            v = 0
            for k in range(5):
                d = table.get(part[pos + k])
                if d is None:
                    raise V2Error("bad container character")
                v = v * base + d
            if v >= 4294967296:
                raise V2Error("container group overflow")
            for sh in (24, 16, 8, 0):
                if len(out) < total:
                    out.append((v >> sh) & 255)
            pos += 5
        if pos < len(part) and len(out) < total:
            raise V2Error("truncated container chunk")
    if len(out) != total:
        raise V2Error("container length mismatch")
    return out


def stream_decrypt(data, p):
    q = 2147483647
    r = (p["v"] + p["o"]) % q
    l = (p["k"] + p["j"]) % q
    ks = p["y"] % 256
    ik = p["t"] % 256
    if r == 0:
        r = 1
    if l == 0:
        l = 1
    out = []
    for n, h in enumerate(data, 1):
        r = (r * 48271) % q
        l = (l * 69621) % q
        e = (r + l + ks + ik * n) % 256
        out.append((h - e) % 256)
        r = (r + h) % q
    return out


def crc16(data):
    x = 0xFFFF ^ 0x152E
    for b in data:
        x ^= b
        for _ in range(8):
            if x & 1:
                x = (x >> 1) ^ 0xA001
            else:
                x >>= 1
    return x


def lz_decompress(src, size):
    out = []
    i = 0
    while i < len(src) and len(out) < size:
        flags = src[i]
        i += 1
        for bit in range(8):
            if i >= len(src):
                return None
            if (flags >> bit) & 1:
                if i + 1 >= len(src):
                    return None
                dist = src[i]
                length = src[i + 1] + 3
                if dist < 1 or dist > len(out) or length > size - len(out):
                    return None
                i += 2
                for _ in range(length):
                    out.append(out[len(out) - dist])
            else:
                out.append(src[i])
                i += 1
            if len(out) == size:
                if i != len(src) or (flags >> (bit + 1)) != 0:
                    return None
                return out
    return None


def le(data, pos, n):
    return sum(data[pos + k] << (8 * k) for k in range(n))


def parse_records(buf, rotation):
    if len(buf) < 9 or buf[0] != 76 or buf[1] != 2:
        raise V2Error("unsupported record header")
    count = buf[2] + buf[3] * 256
    sizes = buf[5]
    flag = buf[7]
    pos = 8
    kb = sizes // 16 + 1
    vb = sizes % 16 + 1
    if flag != 1:
        raise V2Error("unsupported record layout")
    records = {}
    for _ in range(count):
        while True:
            tag = buf[pos]
            if tag == 0:
                pos += 2 + buf[pos + 1]
            elif tag == 1:
                pos += 1
                key = le(buf, pos, kb)
                pos += kb
                if rotation > 0:
                    key = (key >> rotation) + ((key % (1 << rotation)) << (24 - rotation))
                val = le(buf, pos, vb)
                pos += vb
                f2 = le(buf, pos, 2)
                f3 = le(buf, pos + 2, 2)
                f4 = buf[pos + 4]
                f6 = buf[pos + 5]
                f5 = le(buf, pos + 6, 2)
                pos += 8
                f7 = le(buf, pos, vb)
                pos += vb
                records[key] = Record(key, (val, f2, f3, f4, f5, f6, f7))
                break
            else:
                raise V2Error("bad record tag")
    if pos != len(buf) - 1 or buf[-1] != 255:
        raise V2Error("record stream does not end cleanly")
    return records


def load_container(src):
    chunks = parse_chunk_table(src)
    total_chk = 0
    real = {}
    tag_counts = {}
    for a, idx, data, chk in chunks:
        calc = chunk_checksum(a, idx, data)
        if calc != chk:
            raise V2Error("chunk checksum mismatch")
        total_chk = (total_chk + calc) % 65536
        tag_counts[a] = tag_counts.get(a, 0) + 1
    real_tag = max(tag_counts, key=lambda k: tag_counts[k])
    for a, idx, data, chk in chunks:
        if a == real_tag:
            real[idx] = data
    head = re.search(
        r"=\(function\(\)local GS,wS,OS,VS=(\d+),(\d+),(\d+),(\d+) local sS=(\d+) local uS=(\d+) local oS=sS\+uS "
        r"local HS=(\d+) local pS=(\d+) local JS=HS\+pS local QS=(\d+) local TS=(\d+) local YS=QS\+TS "
        r"local nS=(\d+) local BS=(\d+) local tS=nS\+BS local vS,kS=(\d+),(\d+) local FS=(\d+) local mS=(\d+) "
        r'local bS="([^"]+)"local zS=(\d+)',
        src,
    )
    if head is None:
        raise V2Error("container parameters not found")
    g = head.groups()
    params = {
        "o": int(g[4]) + int(g[5]),
        "j": int(g[6]) + int(g[7]),
        "y": int(g[8]) + int(g[9]),
        "t": int(g[10]) + int(g[11]),
        "v": int(g[12]),
        "k": int(g[13]),
    }
    rotation = int(g[14])
    mconst = int(g[15])
    alphabet = g[16]
    total = int(g[17])
    parts = [real[i] for i in range(1, len(real) + 1)]
    raw = base_decode(parts, alphabet, total)
    dec = stream_decrypt(raw, params)
    if dec[0] != 76 or dec[1] != 2:
        raise V2Error("container decryption failed")
    mode = dec[2]
    ulen = le(dec, 3, 3)
    plen = le(dec, 6, 3)
    crc = le(dec, 9, 2)
    payload = dec[11:]
    if plen != len(payload):
        raise V2Error("container payload length mismatch")
    body = lz_decompress(payload, ulen) if mode == 1 else payload
    if body is None or len(body) != ulen or crc16(body) != crc:
        raise V2Error("container integrity check failed")
    records = parse_records(body, rotation)
    return records, mconst, total_chk


class StringParams:
    def __init__(self, src):
        m = re.search(
            r"=\(function\(\)local GS,wS,OS,VS=(\d+),(\d+),(\d+),(\d+) local sS,uS=(\d+),(\d+) local oS=(\d+) "
            r"local HS,pS,JS,QS,TS=(\d+),(\d+),(\d+),(\d+),(\d+) local YS=(\d+) local nS=(\d+) local BS=(\d+) "
            r'local tS=(\d+) local vS="([^"]+)"local kS=(\d+) local FS=(\d+) local mS=(\d+)',
            src,
        )
        if m is None:
            raise V2Error("string cipher parameters not found")
        g = m.groups()
        self.ut, self.tt, self.qt, self.ot = (int(x) for x in g[0:4])
        self.zt, self.gt = int(g[4]), int(g[5])
        self.kinds = {1: int(g[7]), 2: int(g[8]), 3: int(g[9]), 4: int(g[10]), 5: int(g[11])}
        self.state_base = int(g[12])
        self.state_mult = int(g[13])
        self.lt = int(g[15])
        self.alphabet = g[16]
        self.mod = int(g[17])
        last = {}
        for i, c in enumerate(self.alphabet):
            last[c] = i
        self.pairs = last
        self.base = len(self.alphabet)
        self.ma = 48271
        self.mb = 69621


def cipher_bytes(kind, data, a, b, p):
    wt = p.qt % 256
    dt = p.ot % 256
    mod = p.mod
    u = (a + p.ut) % mod
    t = (b + p.tt) % mod
    if u == 0:
        u = 1
    if t == 0:
        t = 1
    out = []
    q = (a + b + wt) % 256
    for i, c in enumerate(data, 1):
        u = (u * p.ma) % mod
        t = (t * p.mb) % mod
        z = (u + t + wt + dt * i) % 256
        if kind == 1:
            x = (c - z - q) % 256
            out.append(x)
            q = (q + c + x + i) % 256
        elif kind == 2:
            out.append((z - c) % 256)
        elif kind == 4:
            g = (c - z) % 256
            x = g % 2
            out.append((g - x) // 2 + x * 128)
        else:
            out.append((c - z) % 256)
        u = (u + c) % mod
    return out


def decode_number(buf, size):
    tag = buf[0]
    if tag <= 240:
        if size != 1:
            return None
        if tag <= 127:
            return tag
        if tag <= 191:
            return 127 - tag
        if tag <= 223:
            return tag - 192
        if tag <= 239:
            return 223 - tag
        return 0
    if tag <= 244:
        if size < 2 or size > 5 or buf[size - 1] == 0:
            return None
        v = 0
        for k in range(size - 1, 0, -1):
            v = v * 256 + buf[k]
        if tag in (242, 244):
            v = -v
        if tag >= 243:
            return float(v)
        return v
    if size != 9:
        return None
    raw = bytes(buf[1:9])
    if tag == 245:
        return struct.unpack("<q", raw)[0]
    if tag == 246:
        return struct.unpack("<d", raw)[0]
    return None


def decode_string(text, mode, nt, f5, rec, p):
    q, t, y = rec.f2, rec.f3, rec.check
    raw = text
    vals = []
    for i in range(0, len(raw), 2):
        c1 = p.pairs.get(chr(raw[i]))
        c2 = p.pairs.get(chr(raw[i + 1])) if i + 1 < len(raw) else None
        if c1 is None or c2 is None:
            return None
        v = c1 * p.base + c2
        if v > 255:
            return None
        vals.append(v)
    z = p.lt * 9973 + mode * 131
    ga = p.zt + nt + q + t + y + z
    gb = p.gt + f5 + q * 3 + t * 5 + y * 7 + z * 3
    sel = (nt + mode * 3 + q * 5 + t * 7 + y * 11) % 4 + 1
    res = cipher_bytes(sel, vals, ga, gb, p)
    if len(res) < 4:
        return None
    n = res[0] + res[1] * 256
    if n != len(res) - 4:
        return None
    acc = 0
    for i in range(1, n + 1):
        acc += res[i + 3] * (i % 251 + 1)
    if acc % 65536 != res[2] + res[3] * 256:
        return None
    body = res[4:]
    kind = p.kinds.get(mode)
    if kind == 1:
        return bytes(body).decode("latin-1")
    if kind in (2, 5):
        return decode_number(body, n)
    if kind == 3:
        return n == 1 and body[0] == 49
    return None


def find_block_table(src, toks, ep, bp):
    best = None
    for i in range(len(toks) - 6):
        if toks[i].val == "local" and toks[i + 2].val == "=" and toks[i + 3].val == "{" and toks[i + 4].val == "function":
            if toks[i + 5].val == "(" and toks[i + 6].val == ")":
                close = bp[i + 3]
                blocks = []
                j = i + 4
                while j < close:
                    if toks[j].val == "function":
                        e = ep[j]
                        blocks.append((j, e))
                        j = e + 1
                    else:
                        j += 1
                if best is None or len(blocks) > len(best[1]):
                    best = (i + 3, blocks)
    if best is None:
        raise V2Error("block table not found")
    return best


class Analysis2:
    def __init__(self, src):
        self.src = src
        self.version = version_of(src)
        self.records, self.mconst, self.chunk_sum = load_container(src)
        self.params = StringParams(src)
        self.toks = tokenize(src)
        self.ep = match_ends(self.toks)
        self.bp = bracket_pairs(self.toks)
        self.table_open, self.block_spans = find_block_table(src, self.toks, self.ep, self.bp)
        self.by_block = {}
        for rec in self.records.values():
            self.by_block.setdefault(rec.block, []).append(rec)
        self.nu_div = None
        self.nu_base = None
        m = re.search(r"=\(\w+-(\d+)\)/(\d+)", src)
        if m:
            self.nu_base, self.nu_div = int(m.group(1)), int(m.group(2))

    def block_count(self):
        return len(self.block_spans)

    def block_source(self, index):
        a, e = self.block_spans[index - 1]
        return self.src[self.toks[a].start:self.toks[e].end]

    def entry_state(self):
        m = re.search(r"return\(\w+\((\d+),\{\}\)\)\(", self.src)
        if m is None:
            return None
        return int(m.group(1))


NOISE_RE = re.compile(r"if (\w+) and \1\(\)then\(nil\)\(\)end")
DECODE_CALL_RE = re.compile(r'\w+\[18\]\(("(?:[^"\\]|\\.)*"),(\d+),(\d+),(\d+),(\d+)\)')
SUCCESSOR_RE = re.compile(r"(\d+)-(\w+)\[3\]")


def block_records(an, index):
    recs = an.by_block.get(index, [])
    return recs[0] if recs else None


def decoded_strings(an):
    out = []
    for bi in range(1, an.block_count() + 1):
        rec = block_records(an, bi)
        if rec is None:
            continue
        for m in DECODE_CALL_RE.finditer(an.block_source(bi)):
            data, _ = read_lua_string(m.group(1), 0)
            val = decode_string(data, int(m.group(2)), int(m.group(3)), int(m.group(4)), rec, an.params)
            out.append((bi, int(m.group(2)), val))
    return out


def lift_block(an, index):
    from .emulate import quote

    rec = block_records(an, index)
    text = NOISE_RE.sub("", an.block_source(index))

    def decode_repl(m):
        data, _ = read_lua_string(m.group(1), 0)
        val = decode_string(data, int(m.group(2)), int(m.group(3)), int(m.group(4)), rec, an.params)
        if isinstance(val, str):
            return quote(val)
        if val is None:
            return "<undecodable>"
        return repr(val) if not isinstance(val, bool) else ("true" if val else "false")

    text = DECODE_CALL_RE.sub(decode_repl, text)
    text = SUCCESSOR_RE.sub(lambda m: str(int(m.group(1)) - rec.f3), text)
    text = re.sub(r"\s+", " ", text)
    return rec, text


def render_blocks(an):
    entry = an.entry_state()
    lines = ["-- protector v%s, %d states, entry state %s" % (".".join(str(x) for x in (an.version or ())), len(an.records), entry)]
    for bi in range(1, an.block_count() + 1):
        rec, text = lift_block(an, bi)
        mark = " (entry)" if rec is not None and rec.key == entry else ""
        lines.append("")
        lines.append("-- state %d%s: block %d, operator set %d" % (rec.key, mark, bi, rec.op_set))
        lines.append(text)
    return "\n".join(lines) + "\n"
