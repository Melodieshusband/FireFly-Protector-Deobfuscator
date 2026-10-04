import re
from .lexer import bracket_pairs, match_ends, tokenize


class Program:
    def __init__(self, src):
        self.src = src
        self.toks = tokenize(src)
        self.bp = bracket_pairs(self.toks)
        self.ep = match_ends(self.toks)
        self.starts = {t.start: i for i, t in enumerate(self.toks)}
        self.locate_g()
        self.blocks = self.find_blocks()

    def locate_g(self):
        m = re.search(
            r"(?P<name>\w+)=function\((?P<p1>\w+),(?P<p2>\w+),(?P<p3>\w+),(?P<p4>\w+)\)"
            r"(?P<junk>(?:local \w+=\{[^{}]*\}\s*)*)"
            r"local (?P<locs>(?:\w+,)*\w+) if (?P<flag>\w+) then local \w+=#\w+\+\d+",
            self.src,
        )
        if not m:
            raise ValueError("executor function not found")
        self.g_name = m.group("name")
        self.g_params = [m.group("p1"), m.group("p2"), m.group("p3"), m.group("p4")]
        self.g_junk = []
        for jm in re.finditer(r"local (\w+)=\{([^{}]*)\}", m.group("junk")):
            items = [x.strip() for x in re.split(r"[,;]", jm.group(2)) if x.strip()]
            if any(x != "nil" for x in items):
                raise ValueError("unsupported executor preamble")
            self.g_junk.append((jm.group(1), len(items)))
        junk_names = [name for name, _ in self.g_junk]
        self.g_locals = set(m.group("locs").split(",")) | set(self.g_params) | set(junk_names)
        self.flag_name = m.group("flag")
        self.g_idx = self.starts[m.start() + len(m.group("name")) + 1]

    def find_blocks(self):
        toks = self.toks
        g_idx = self.g_idx
        g_end = self.ep[g_idx]
        c5 = None
        for i in range(g_idx, g_end):
            if toks[i].kind == "name" and toks[i + 1].val == "=" and toks[i + 2].val == "{" and toks[i + 3].val == "function" and toks[i + 4].val == "(":
                c5 = i + 2
                self.c5_name = toks[i].val
                break
        close = self.bp[c5]
        blocks = []
        i = c5 + 1
        while i < close:
            if toks[i].val == "function":
                e = self.ep[i]
                blocks.append((i, e))
                i = e + 1
            else:
                i += 1
        self.g_locals.add(self.c5_name)
        self.g_range = (g_idx, g_end)
        self.c5_range = (c5, close)
        return blocks

    def body_tokens(self, k):
        s, e = self.blocks[k]
        j = self.bp[s + 1] + 1
        return self.toks[j:e]

    def outer_function(self):
        toks = self.toks
        for i, t in enumerate(toks):
            if t.val == "function" and t.kind == "kw" and toks[i + 1].val == "(":
                close = self.bp[i + 1]
                params = [x.val for x in toks[i + 2:close] if x.kind == "name"]
                if len(params) >= 20 and self.ep.get(i) is not None and self.toks[self.ep[i] + 1].val == ")" and self.toks[self.ep[i] + 2].val == "(":
                    end = self.ep[i]
                    call = end + 1
                    if toks[call].val == ")" and toks[call + 1].val == "(":
                        ao = call + 1
                        ac = self.bp[ao]
                        args = []
                        cur = []
                        depth = 0
                        for k in range(ao + 1, ac):
                            x = toks[k]
                            if x.kind == "op" and x.val in "({[":
                                depth += 1
                            if x.kind == "op" and x.val in ")}]":
                                depth -= 1
                            if x.kind == "op" and x.val == "," and depth == 0:
                                args.append(cur)
                                cur = []
                            else:
                                cur.append(x)
                        args.append(cur)
                        return {"index": i, "end": end, "params": params, "args": args, "params_end": toks[close].end}
        raise ValueError("outer function not found")

    def dispatcher(self):
        src = self.src
        c5 = re.escape(self.c5_name)
        m = re.search(r"(\w+)=(\w+)\((\w+),(\w+),(\w+)\)\s*(\w+)=(\w+)((?:\s*if \1\[6\]==\d+ then \6=\w+ end)*)\s*(\w+)=\6\[\1\[4\]\]\s*" + c5 + r"\[\1\[1\]\]\(\)", src)
        if not m:
            raise ValueError("dispatcher not found")
        rec, state_fn, data, state, salt_var, tmp, first, extra, ops = m.groups()[:9]
        containers = {1: first}
        for cid, name in re.findall(r"if %s\[6\]==(\d+) then %s=(\w+) end" % (re.escape(rec), re.escape(tmp)), extra):
            containers[int(cid)] = name
        salt = re.search(r"%s=\(\(%s\+(\d+)\)" % (re.escape(salt_var), re.escape(state)), src)
        gsrc = src[self.toks[self.g_range[0]].start:self.toks[self.g_range[1]].end]
        tails = re.findall(r"return (\w+)\((\w+)\[2\]\((\w+)\)\)end", gsrc)
        tail = tails[-1]
        return {
            "rec": rec,
            "state_fn": state_fn,
            "state_data": data,
            "state": state,
            "salt_var": salt_var,
            "ops": ops,
            "containers": containers,
            "salt": int(salt.group(1)),
            "unpack": tail[0],
            "store_get": tail[1],
            "ret": tail[2],
        }
