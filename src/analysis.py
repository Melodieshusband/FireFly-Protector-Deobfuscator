import re
from .ciphers import StringDecoder, T_HEAD
from .container import load_state_table
from .folding import is_const
from .lifter import Lifter
from .parser import LuaParser
from .program import Program


class Analysis:
    def __init__(self, src):
        self.src = src
        self.P = Program(src)
        self.T, self.wp, self.chunk_total = load_state_table(src)
        self.D = StringDecoder(src)
        self.disp = self.P.dispatcher()
        self.outer = self.P.outer_function()
        self.reg_names = set(self.P.g_locals)
        self.warnings = []
        self.find_names()
        self.build_aliases()
        self.ops = self.build_ops()
        self.blocks = [LuaParser(self.P.body_tokens(k)).block() for k in range(len(self.P.blocks))]
        self.prologue = self.find_prologue()
        self.lifted = {}
        self.entry = self.find_entry()

    def find_names(self):
        src = self.src
        n = {}
        m = re.search(r"(\w+)=function\(\)(\w+)=(?:1\+\2|\2\+1) (\w+)\[\2\]=1 return \2 end", src)
        n["alloc"] = m.group(1)
        m = re.search(r"(\w+)=function\((\w+)\)(\w+)\[\2\]=\3\[\2\]-1 if (?:\3\[\2\]==0|0==\3\[\2\])\s*then \3\[\2\],(\w+)\[\2\]=nil,nil end end", src)
        n["release"] = m.group(1)
        n["store"] = m.group(4)
        m = re.search(r"(\w+)=function\((\w+)\)local (\w+),(\w+)=1,\2\[1\]while", src)
        n["gc"] = m.group(1) if m else None
        m = re.search(r"(\w+)=\(function\(\)local \w+=\{\}local \w+=\{\}local \w+=(\d+) local \w+=(\d+) local \w+=(\d+) local \w+=function", src)
        n["handles"] = m.group(1)
        m = T_HEAD.search(src)
        n["decoder"] = m.group(1)
        gname = self.P.g_name
        ctors = {}
        for c in re.finditer(r"(\w+)=function\((\w+),(\w+)\)local (\w+)=(\w+)\(\3\)local (\w+)=function\(([^)]*)\)return %s\(\2,\{[^}]*\},\3,\4\)end return \6 end" % re.escape(gname), src):
            params = c.group(7).strip()
            ctors[c.group(1)] = "..." if params == "..." else (len([p for p in params.split(",") if p.strip()]))
        n["ctors"] = ctors
        self.names = n

    def build_aliases(self):
        o = self.outer
        src = self.src
        alias = {}
        for name, arg in zip(o["params"], o["args"]):
            text = src[arg[0].start:arg[-1].end].strip() if arg else "nil"
            alias[name] = self.alias_value(text)
        env_name = o["params"][0]
        alias[env_name] = ("env",)
        pat = re.compile(r"(?<![\w.])(\w+)=%s\[((?:\(*\"[^\"]*\"\)*\.\.)*\(*\"[^\"]*\"\)*)\]" % re.escape(env_name))
        for m in pat.finditer(src):
            if m.group(1) in o["params"]:
                lib = "".join(re.findall(r"\"([^\"]*)\"", m.group(2)))
                alias[m.group(1)] = ("builtin", lib)
        self.alias = alias
        self.env_name = env_name

    def alias_value(self, text):
        if text == "nil":
            return ("const", None)
        if text.startswith("_ENV"):
            return ("env",)
        if text == "{...}":
            return ("varargs",)
        if text.startswith("(function"):
            return ("helper",)
        m = re.fullmatch(r"(\w+)\[\"(\w+)\"\]", text)
        if m:
            return ("builtin", m.group(1) + "." + m.group(2))
        return ("builtin", text)

    def build_ops(self):
        P = self.P
        toks = P.toks
        out = []
        for cid, cname in sorted(self.disp["containers"].items()):
            start = None
            for i, t in enumerate(toks):
                if t.kind == "name" and t.val == cname and toks[i + 1].val == "=" and toks[i + 2].val == "{" and toks[i + 3].val == "{":
                    start = i + 2
                    break
            if start is None:
                raise ValueError("operator tables not found")
            close = P.bp[start]
            subs = []
            i = start + 1
            while i < close:
                if toks[i].val == "{":
                    subs.append((i, P.bp[i]))
                    i = P.bp[i] + 1
                else:
                    i += 1
            tabs = []
            for a, b in subs:
                d = {}
                j = a + 1
                while j < b:
                    if toks[j].val == "[":
                        key = int(toks[j + 1].val)
                        fe = P.ep[j + 4]
                        si = P.starts[toks[j + 4].start]
                        ei = P.starts[toks[fe].start]
                        node = LuaParser(toks[si:ei + 1]).simple()
                        d[key] = node
                        j = fe + 1
                    else:
                        j += 1
                tabs.append(d)
            out.append(tabs)
        return out

    def find_prologue(self):
        s, e = self.P.g_range
        text = self.src[self.P.toks[s].start:self.P.toks[e].end]
        res = []
        for name, count in self.P.g_junk:
            res.append((name, ("table", [("pos", ("const", None))] * count)))
        m = re.search(r"(\w+)=\(function\(\)local \w+=(\w+) return \w+ and\(\w+\.create and true\)or false end\)\(\)", text)
        if m:
            res.append((m.group(1), ("or", ("and", ("index", self.alias_ref(m.group(2)), ("const", "create")), ("const", True)), ("const", False))))
        return res

    def alias_ref(self, name):
        return self.alias.get(name, ("builtin", name))

    def find_entry(self):
        m = re.search(r"return\((\w+)\((\d+),\{\}\)\)\((\w+)\((\w+)\)\)end\)", self.src)
        if not m:
            raise ValueError("entry point not found")
        return int(m.group(2)), m.group(1)

    def decode_const(self, args):
        if not args or not all(is_const(a) for a in args):
            return None
        vals = [a[1] for a in args]
        if type(vals[0]) is not str or len(vals) < 2:
            return None
        try:
            rest = [int(x) if x is not None else 0 for x in vals[2:]]
            r = self.D.decode(vals[0], int(vals[1]), *rest)
        except Exception:
            return None
        if r is None:
            return None
        if type(r) is tuple:
            return r
        return ("const", r)

    def lift(self, key):
        r = self.lifted.get(key)
        if r is None:
            r = Lifter(self, key).run()
            self.lifted[key] = r
        return r
