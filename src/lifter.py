from .errors import BlockFail
from .folding import is_const, mk_and, mk_bin, mk_or, mk_un


class Lifter:
    def __init__(self, an, key):
        self.an = an
        self.key = key
        self.rec = an.T.get(key)
        if self.rec is None:
            raise BlockFail("unknown state %r" % (key,))
        self.d3 = self.rec[2]
        tabs = an.ops[0]
        sub = self.rec[3] - 1
        if sub >= len(tabs):
            sub = len(tabs) - 1
        self.optab = tabs[sub]
        self.block = an.blocks[self.rec[0] - 1]
        self.stmts = []
        self.regs = {}
        self.scopes = [{}]
        self.tid = 0
        self.next_expr = None
        self.ret_expr = None
        self.ret_set = False
        self.ncall = 0

    def new_tmp(self, expr):
        self.tid += 1
        tid = (self.key, self.tid)
        self.stmts.append(("let", tid, expr))
        return ("tmp", tid)

    def run(self):
        for st in self.block:
            self.exec_stmt(st)
        nxt = self.resolve_next()
        writes = {}
        for r, v in self.regs.items():
            if v == ("reg", r):
                continue
            writes[r] = v
        return {"key": self.key, "rec": self.rec, "stmts": self.stmts, "writes": writes, "next": nxt, "ret": self.ret_expr}

    def resolve_next(self):
        e = self.next_expr
        if e is None:
            raise BlockFail("state %r: no transition" % (self.key,))
        return self.to_next(e)

    def to_next(self, e):
        if is_const(e):
            if e[1] is None:
                return ("ret",)
            if type(e[1]) in (int, float):
                return ("goto", int(e[1]))
        if e[0] == "or" and e[1][0] == "and" and is_const(e[1][2]) and type(e[1][2][1]) in (int, float):
            other = self.to_next(e[2])
            return ("branch", e[1][1], ("goto", int(e[1][2][1])), other)
        if e[0] == "and" and is_const(e[2]) and type(e[2][1]) in (int, float):
            return ("branch", e[1], ("goto", int(e[2][1])), ("ret",))
        raise BlockFail("state %r: unsupported transition %r" % (self.key, e))

    def lookup(self, name):
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        an = self.an
        d = an.disp
        gp = an.P.g_params
        if name == d["state"]:
            return self.regs.get(name, ("const", None))
        if name == d["rec"]:
            return ("rec",)
        if name == d["ops"]:
            return ("ops",)
        if name == gp[2]:
            return ("caps",)
        if name == gp[1]:
            return ("args_handle",)
        if name == d["ret"]:
            return self.regs.get(name, ("const", None))
        if name in an.reg_names or name == an.P.c5_name:
            if name in self.regs:
                return self.regs[name]
            return ("reg", name)
        nm = an.names
        if name == nm["store"]:
            return ("store",)
        if name == nm["handles"]:
            return ("handles",)
        if name == nm["alloc"]:
            return ("allocfn",)
        if name == nm["release"] or name == nm["gc"]:
            return ("releasefn",)
        if name == nm["decoder"]:
            return ("decoderfn",)
        if name in nm["ctors"]:
            return ("ctor", name)
        if name in an.alias:
            return an.alias[name]
        return ("unknown", name)

    def assign_name(self, name, v):
        for sc in reversed(self.scopes):
            if name in sc:
                sc[name] = v
                return
        an = self.an
        d = an.disp
        if name == d["state"]:
            self.next_expr = v
            self.regs[name] = v
            return
        if name == d["ret"]:
            self.ret_expr = v
            self.ret_set = True
            self.regs[name] = v
            return
        if name in an.reg_names:
            self.regs[name] = v
            return
        raise BlockFail("state %r: write to unknown name %r" % (self.key, name))

    def exec_stmt(self, st):
        k = st[0]
        if k == "local":
            vals = self.ev_list(st[2], len(st[1]))
            sc = self.scopes[-1]
            for n, v in zip(st[1], vals):
                sc[n] = v
        elif k == "assign":
            targets = st[1]
            vals = self.ev_list(st[2], len(targets))
            for t, v in zip(targets, vals):
                self.assign_target(t, v)
        elif k == "call":
            v = self.ev(st[1])
            if v[0] == "tmp":
                return
        else:
            raise BlockFail("state %r: unsupported statement %r" % (self.key, k))

    def assign_target(self, t, v):
        if t[0] == "name":
            self.assign_name(t[1], v)
            return
        if t[0] == "index":
            obj = self.ev(t[1])
            key = self.ev(t[2])
            if obj[0] == "store":
                self.stmts.append(("cellset", key, v))
                return
            if obj[0] == "env":
                self.stmts.append(("setglobal", key, v))
                return
            self.stmts.append(("setindex", obj, key, v))
            return
        raise BlockFail("state %r: bad assignment target" % (self.key,))

    def ev_list(self, nodes, want=None):
        out = []
        for i, n in enumerate(nodes):
            v = self.ev(n)
            if i == len(nodes) - 1 and n[0] in ("callx", "method") and (v[0] == "tmp" or v[0] == "unpack"):
                if want is not None and want > len(nodes):
                    out.append(("multi", v))
                    for j in range(1, want - len(nodes) + 1):
                        out.append(("first", ("select", j + 1, v)))
                    break
                out.append(v)
            else:
                out.append(self.single(v))
        if want is not None:
            while len(out) < want:
                out.append(("const", None))
        return out

    def single(self, v):
        if v[0] == "unpack":
            return ("first", v)
        return v

    def args_list(self, nodes):
        out = []
        for i, n in enumerate(nodes):
            v = self.ev(n)
            if i == len(nodes) - 1 and n[0] in ("callx", "method") and (v[0] == "tmp" or v[0] == "unpack"):
                out.append(("multi", v))
            else:
                out.append(self.single(v))
        return out

    def ev(self, n):
        k = n[0]
        if k == "const":
            return n
        if k == "name":
            return self.lookup(n[1])
        if k == "paren":
            v = self.ev(n[1])
            return self.single(v)
        if k == "bin":
            return mk_bin(n[1], self.single(self.ev(n[2])), self.single(self.ev(n[3])))
        if k == "un":
            return mk_un(n[1], self.single(self.ev(n[2])))
        if k == "and":
            return mk_and(self.single(self.ev(n[1])), self.single(self.ev(n[2])))
        if k == "or":
            return mk_or(self.single(self.ev(n[1])), self.single(self.ev(n[2])))
        if k == "index":
            return self.ev_index(self.single(self.ev(n[1])), self.single(self.ev(n[2])))
        if k == "table":
            return self.ev_table(n)
        if k == "callx":
            return self.ev_call(n)
        if k == "vararg":
            return ("vararg",)
        raise BlockFail("state %r: unsupported expression %r" % (self.key, k))

    def ev_table(self, n):
        items = []
        entries = n[1]
        for i, it in enumerate(entries):
            if it[0] == "pos":
                v = self.ev(it[1])
                if i == len(entries) - 1 and it[1][0] in ("callx", "method") and (v[0] == "tmp" or v[0] == "unpack"):
                    items.append(("pos", ("multi", v)))
                else:
                    items.append(("pos", self.single(v)))
            else:
                items.append(("kv", self.single(self.ev(it[1])), self.single(self.ev(it[2]))))
        return ("table", items)

    def ev_index(self, obj, key):
        t = obj[0]
        if t == "rec" and is_const(key):
            return ("const", self.rec[int(key[1]) - 1])
        if t == "ops" and is_const(key):
            return ("opfn", int(key[1]))
        if t == "caps" and is_const(key):
            return ("cap", int(key[1]))
        if t == "store":
            return ("cellref", key)
        if t == "handles" and is_const(key):
            return ("handle", int(key[1]))
        if t == "args" and is_const(key):
            return ("arg", int(key[1]))
        if t == "env":
            return self.new_tmp(("global", key))
        if t == "pack" or t == "table":
            items = obj[1]
            if len(items) == 1 and items[0][0] == "pos" and items[0][1][0] == "multi" and is_const(key) and type(key[1]) is int and key[1] >= 1:
                return ("first", ("select", key[1], items[0][1][1]))
        return self.new_tmp(("index", obj, key))

    def apply_op(self, kk, args):
        node = self.optab.get(kk)
        if node is None:
            for tab in self.an.ops[0]:
                if kk in tab:
                    node = tab[kk]
                    break
        if node is None:
            raise BlockFail("state %r: unknown operator %r" % (self.key, kk))
        params = node[1]
        body = node[3]
        scope = {}
        for i, p in enumerate(params):
            scope[p] = args[i] if i < len(args) else ("const", None)
        if len(body) != 1 or body[0][0] != "return" or len(body[0][1]) != 1:
            raise BlockFail("state %r: operator body shape" % (self.key,))
        self.scopes.append(scope)
        try:
            return self.single(self.ev_op(body[0][1][0]))
        finally:
            self.scopes.pop()

    def ev_op(self, n):
        k = n[0]
        if k == "name":
            for sc in reversed(self.scopes):
                if n[1] in sc:
                    return sc[n[1]]
            raise BlockFail("operator references outer name %r" % n[1])
        if k == "const":
            return n
        if k == "bin":
            return mk_bin(n[1], self.ev_op(n[2]), self.ev_op(n[3]))
        if k == "un":
            return mk_un(n[1], self.ev_op(n[2]))
        if k == "and":
            return mk_and(self.ev_op(n[1]), self.ev_op(n[2]))
        if k == "or":
            return mk_or(self.ev_op(n[1]), self.ev_op(n[2]))
        if k == "paren":
            return self.ev_op(n[1])
        if k == "index":
            return self.new_tmp(("index", self.ev_op(n[1]), self.ev_op(n[2])))
        raise BlockFail("operator expression %r" % k)

    def ev_call(self, n):
        fnode = n[1]
        f = self.single(self.ev(fnode))
        raw_args = n[2]
        t = f[0]
        if t == "opfn":
            args = [self.single(self.ev(a)) for a in raw_args]
            return self.apply_op(f[1], args)
        if t == "handle":
            args = [self.single(self.ev(a)) for a in raw_args]
            v = args[0] if args else ("const", None)
            if f[1] == 1:
                return v
            if v[0] == "args_handle":
                return ("args",)
            if v[0] == "cellref":
                return self.new_tmp(("cellval", v[1]))
            return v
        if t == "decoderfn":
            args = [self.single(self.ev(a)) for a in raw_args]
            r = self.an.decode_const(args)
            if r is not None:
                return r if r[0] == "const" else self.new_tmp(r)
            self.an.warnings.append("state %r: constant did not decode" % (self.key,))
            return self.new_tmp(("call", ("builtin", "decode_failed"), args))
        if t == "allocfn":
            return self.new_tmp(("alloc",))
        if t == "releasefn":
            for a in raw_args:
                self.ev(a)
            return ("const", None)
        if t == "ctor":
            args = [self.single(self.ev(a)) for a in raw_args]
            if len(args) < 2 or not is_const(args[0]):
                raise BlockFail("state %r: closure key is not constant" % (self.key,))
            caps = args[1]
            if caps[0] != "table":
                raise BlockFail("state %r: closure captures not a table" % (self.key,))
            capl = [it[1] for it in caps[1] if it[0] == "pos"]
            return self.new_tmp(("closure", int(args[0][1]), capl, self.an.names["ctors"][f[1]]))
        if t == "builtin" and f[1] == "unpack":
            args = [self.single(self.ev(a)) for a in raw_args]
            a0 = args[0] if args else ("const", None)
            if a0[0] == "table" and len(a0[1]) == 1 and a0[1][0][0] == "pos" and a0[1][0][1][0] == "multi":
                return a0[1][0][1][1]
            return ("unpack", a0)
        args = self.args_list(raw_args)
        return self.new_tmp(("call", f, args))
