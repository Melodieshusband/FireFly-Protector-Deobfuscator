import copy
import functools
import math
import random
import re
from .folding import num_norm
from .lexer import unescape
from .parser import INF
from .values import ARITH, Bi, COMPARE, Clo, Frame, LuaErr, Obj, Stuck, Sym, is_sym, isnum, lt, lua_pat, nk, tonum, tostr


DEFAULT_ASSUMED = frozenset(
    "wait spawn delay tick time warn game workspace script shared task typeof Instance Vector3 Vector2 CFrame Color3 UDim UDim2 Enum os coroutine debug utf8 bit32".split()
)

UNMODELED = {
    "string": frozenset("dump pack packsize unpack gfind".split()),
    "table": frozenset("maxn move getn foreach foreachi".split()),
    "math": frozenset("frexp ldexp maxinteger mininteger randomseed tointeger type ult".split()),
}


class Evaluator:
    def __init__(self, an, step_limit=400000, assume=DEFAULT_ASSUMED):
        self.an = an
        self.assume = frozenset(assume)
        self.steps = 0
        self.step_limit = step_limit
        self.oid = 0
        self.rng = random.Random(7)
        self.symn = 0
        self.res = []
        self.expr_cache = {}
        self.decoder = None
        self.decoded = {}
        self.need_keys = []
        self.G = self.new_obj()
        self.lib = {}
        self.depth = 0
        self.build_env()
        self.seed_globals()

    def new_obj(self, lib=None):
        self.oid += 1
        return Obj(self.oid, lib)

    def new_sym(self, prefix="v"):
        self.symn += 1
        return Sym("%s%d" % (prefix, self.symn))

    def seed_globals(self):
        m = re.search(r"\(\"((?:[^\"\\]|\\.)*)\"\):gsub\(\".\+\",function\((\w+)\)(\w+)=\2 end\)", self.an.src)
        if m:
            self.G.d[m.group(3)] = unescape(m.group(1))

    def build_env(self):
        E = self
        string = self.new_obj("string")
        table = self.new_obj("table")
        mathlib = self.new_obj("math")
        self.lib = {"string": string, "table": table, "math": mathlib}

        def L(name, fn, kind="pure"):
            return Bi(name, fn, kind)

        def s_byte(s, i=1, j=None):
            s = tostr(s)
            i = int(i)
            j = i if j is None else int(j)
            if i < 0:
                i = len(s) + i + 1
            if j < 0:
                j = len(s) + j + 1
            return [ord(c) for c in s[max(i, 1) - 1:j]]

        def s_char(*a):
            return ["".join(chr(int(x) & 255) for x in a)]

        def s_len(s):
            return [len(tostr(s))]

        def s_sub(s, i=1, j=-1):
            s = tostr(s)
            i = int(i)
            j = int(j)
            n = len(s)
            if i < 0:
                i = max(n + i + 1, 1)
            if i == 0:
                i = 1
            if j < 0:
                j = n + j + 1
            return [s[i - 1:j]]

        def s_gsub(s, pat, rep, maxn=None):
            s = tostr(s)
            rx = lua_pat(pat)
            cnt = [0]

            def fn(m):
                cnt[0] += 1
                caps = list(m.groups()) if m.re.groups else [m.group(0)]
                if type(rep) is str:
                    return re.sub(r"%(\d)", lambda mm: caps[int(mm.group(1)) - 1] if mm.group(1) != "0" else m.group(0), rep.replace("%%", "\0")).replace("\0", "%")
                if type(rep) is Obj:
                    v = E.index(rep, caps[0])
                else:
                    r = E.call(rep, caps)
                    v = r[0] if r else None
                return m.group(0) if v is None or v is False else tostr(v)

            res = rx.sub(fn, s, count=0 if maxn is None else int(maxn))
            return [res, cnt[0]]

        def s_gmatch(s, pat):
            s = tostr(s)
            rx = lua_pat(pat)
            it = rx.finditer(s)

            def step(*_):
                m = next(it, None)
                if m is None:
                    return [None]
                return list(m.groups()) if rx.groups else [m.group(0)]

            return [Bi("gmatch_iter", step)]

        def s_find(s, pat, init=1, plain=None):
            s = tostr(s)
            if plain:
                p = s.find(pat, int(init) - 1)
                return [None] if p < 0 else [p + 1, p + len(pat)]
            m = lua_pat(pat).search(s, int(init) - 1)
            if not m:
                return [None]
            return [m.start() + 1, m.end()] + list(m.groups())

        def s_match(s, pat, init=1):
            s = tostr(s)
            m = lua_pat(pat).search(s, int(init) - 1)
            if not m:
                return [None]
            return list(m.groups()) if m.re.groups else [m.group(0)]

        FORMAT_SPEC = re.compile(r"%([-+ #0]*)(\d*)(?:\.(\d+))?([A-Za-z%])")

        def s_format(fmt=None, *a):
            fmt = tostr(fmt)
            pos = [0]

            def take():
                if pos[0] >= len(a):
                    raise LuaErr("input:1: bad argument #%d to 'format' (no value)" % (pos[0] + 2))
                v = a[pos[0]]
                pos[0] += 1
                return v

            def integer(v):
                x = tonum(v) if type(v) is str else v
                if not isnum(x):
                    raise LuaErr("input:1: bad argument #%d to 'format' (number expected, got %s)" % (pos[0] + 1, lt(v)))
                if x != x or x in (INF, -INF) or float(x) != int(x):
                    raise LuaErr("input:1: bad argument #%d to 'format' (number has no integer representation)" % (pos[0] + 1))
                return int(x)

            def one(m):
                flags, width, prec, conv = m.groups()
                if conv == "%":
                    return "%"
                spec = "%" + flags + width + ("." + prec if prec is not None else "")
                if conv in "diu":
                    return (spec + "d") % integer(take())
                if conv in "oxX":
                    v = integer(take())
                    return (spec + conv) % (v & 0xFFFFFFFFFFFFFFFF if v < 0 else v)
                if conv == "c":
                    return ("%" + flags + width + "s") % chr(integer(take()) & 255)
                if conv in "eEfFgG":
                    v = take()
                    x = tonum(v) if type(v) is str else v
                    if not isnum(x):
                        raise LuaErr("input:1: bad argument #%d to 'format' (number expected, got %s)" % (pos[0] + 1, lt(v)))
                    return (spec + conv) % float(x)
                if conv == "s":
                    return (spec + "s") % g_tostring(take())[0]
                if conv == "q":
                    v = take()
                    if type(v) is not str:
                        raise Stuck("format %q of non string")
                    body = v.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\\n").replace("\r", "\\r").replace("\0", "\\0")
                    return '"' + body + '"'
                raise Stuck("format conversion %%%s" % conv)

            return [FORMAT_SPEC.sub(one, fmt)]

        string.d["format"] = L("string.format", s_format)
        string.d["reverse"] = L("string.reverse", lambda s: [tostr(s)[::-1]])

        for n, f in (("byte", s_byte), ("char", s_char), ("len", s_len), ("sub", s_sub), ("gsub", s_gsub), ("gmatch", s_gmatch), ("find", s_find), ("match", s_match)):
            string.d[n] = L("string." + n, f)
        string.d["rep"] = L("string.rep", lambda s, n: [tostr(s) * int(n)])
        string.d["lower"] = L("string.lower", lambda s: [tostr(s).lower()])
        string.d["upper"] = L("string.upper", lambda s: [tostr(s).upper()])
        self.string = string

        def t_concat(t, sep="", i=1, j=None):
            j = E.length(t) if j is None else int(j)
            return [tostr(sep).join(tostr(E.index(t, k)) for k in range(int(i), j + 1))]

        def t_unpack(t, i=1, j=None):
            j = E.length(t) if j is None else int(j)
            return [E.index(t, k) for k in range(int(i), j + 1)]

        def t_pack(*a):
            t = E.new_obj()
            for i, v in enumerate(a, 1):
                if v is not None:
                    t.d[i] = v
            t.d["n"] = len(a)
            return [t]

        def t_create(n, v=None):
            t = E.new_obj()
            if v is not None:
                for i in range(1, int(n) + 1):
                    t.d[i] = v
            return [t]

        def t_insert(t, a, b=None):
            if b is None:
                t.d[E.length(t) + 1] = a
            else:
                n = E.length(t)
                for k in range(n, int(a) - 1, -1):
                    t.d[k + 1] = t.d.get(k)
                t.d[int(a)] = b
            return []

        def t_remove(t, p=None):
            n = E.length(t)
            p = n if p is None else int(p)
            v = t.d.get(p)
            for k in range(p, n):
                t.d[k] = t.d.get(k + 1)
            t.d.pop(n, None)
            return [v]

        def t_sort(t, cmp=None):
            n = E.length(t)
            items = [E.index(t, k) for k in range(1, n + 1)]
            if cmp is None:
                def less(a, b):
                    return E.truth(E.binop("<", a, b))
            else:
                def less(a, b):
                    return E.truth(E.call_first(cmp, [a, b]))

            def order(a, b):
                if less(a, b):
                    return -1
                if less(b, a):
                    return 1
                return 0

            items.sort(key=functools.cmp_to_key(order))
            for k, v in enumerate(items, 1):
                t.d[k] = v
            return []

        table.d["sort"] = L("table.sort", t_sort, "heap")
        table.d["concat"] = L("table.concat", t_concat)
        table.d["unpack"] = L("table.unpack", t_unpack)
        table.d["pack"] = L("table.pack", t_pack)
        table.d["create"] = L("table.create", t_create)
        table.d["insert"] = L("table.insert", t_insert, "heap")
        table.d["remove"] = L("table.remove", t_remove, "heap")

        def m_floor(x):
            x = tonum(x)
            if x is None:
                raise LuaErr("input:1: bad argument #1 to 'floor' (number expected)")
            return [num_norm(float(math.floor(x)))] if abs(x) < 2 ** 53 else [x]

        def mnum(x, name):
            v = tonum(x) if type(x) is str else x
            if not isnum(v):
                raise LuaErr("input:1: bad argument #1 to '%s' (number expected, got %s)" % (name, "no value" if x is None else lt(x)))
            return float(v)

        def m_wrap(name, fn):
            def w(x=None, *rest):
                try:
                    return [num_norm(fn(mnum(x, name), *[mnum(r, name) for r in rest]))]
                except (ValueError, ZeroDivisionError):
                    return [float("nan")]
                except OverflowError:
                    return [INF]
            return w

        def m_ceil(x=None):
            v = mnum(x, "ceil")
            if v != v or v in (INF, -INF) or abs(v) >= 2 ** 53:
                return [num_norm(v)]
            return [num_norm(float(math.ceil(v)))]

        def m_log(x=None, base=None):
            v = mnum(x, "log")
            if v == 0:
                return [-INF]
            if v < 0 or v != v:
                return [float("nan")]
            if base is None:
                return [num_norm(math.log(v))]
            b = mnum(base, "log")
            if b == 10:
                return [num_norm(math.log10(v))]
            if b == 2:
                return [num_norm(math.log2(v))]
            return [num_norm(math.log(v) / math.log(b))]

        def m_modf(x=None):
            v = mnum(x, "modf")
            if v in (INF, -INF):
                return [v, 0.0]
            frac, whole = math.modf(v)
            return [num_norm(whole), num_norm(frac)]

        def m_atan(y=None, x=None):
            if x is None:
                return [num_norm(math.atan(mnum(y, "atan")))]
            return [num_norm(math.atan2(mnum(y, "atan"), mnum(x, "atan")))]

        for n, f in (("sqrt", math.sqrt), ("sin", math.sin), ("cos", math.cos), ("tan", math.tan), ("asin", math.asin), ("acos", math.acos), ("exp", math.exp), ("deg", math.degrees), ("rad", math.radians), ("fmod", math.fmod), ("pow", math.pow), ("log10", math.log10), ("sinh", math.sinh), ("cosh", math.cosh), ("tanh", math.tanh)):
            mathlib.d[n] = L("math." + n, m_wrap(n, f))
        mathlib.d["ceil"] = L("math.ceil", m_ceil)
        mathlib.d["log"] = L("math.log", m_log)
        mathlib.d["modf"] = L("math.modf", m_modf)
        mathlib.d["atan"] = L("math.atan", m_atan)
        mathlib.d["atan2"] = L("math.atan2", m_atan)
        mathlib.d["pi"] = math.pi
        mathlib.d["floor"] = L("math.floor", m_floor)
        mathlib.d["huge"] = INF
        mathlib.d["abs"] = L("math.abs", lambda x: [abs(x)])
        mathlib.d["max"] = L("math.max", lambda *a: [max(a)])
        mathlib.d["min"] = L("math.min", lambda *a: [min(a)])
        def m_random(a=None, b=None):
            if a is None:
                return [E.rng.random()]
            if b is None:
                return [E.rng.randint(1, int(a))]
            return [E.rng.randint(int(a), int(b))]

        mathlib.d["random"] = L("math.random", m_random)

        def g_error(msg=None, level=1):
            if type(msg) is str and level != 0:
                msg = "input:1: " + msg
            raise LuaErr(msg)

        def g_pcall(f=None, *a):
            try:
                return [True] + E.call(f, list(a))
            except LuaErr as e:
                return [False, e.value]

        def g_select(n, *a):
            if n == "#":
                return [len(a)]
            n = int(n)
            return list(a[n - 1:]) if n > 0 else list(a[n:])

        def g_setmt(t, m):
            t.mt = m
            return [t]

        def g_getmt(t):
            if type(t) is Obj and t.mt is not None:
                p = t.mt.d.get("__metatable")
                return [p if p is not None else t.mt]
            return [None]

        def g_next(t, k=None):
            keys = list(t.d.keys())
            if k is None:
                idx = 0
            else:
                kk = nk(k)
                idx = keys.index(kk) + 1 if kk in keys else len(keys)
            if idx >= len(keys):
                return [None]
            kk = keys[idx]
            return [kk[1] if type(kk) is tuple else kk, t.d[kk]]

        def g_assert(v=None, msg="assertion failed!"):
            if v is None or v is False:
                raise LuaErr(msg)
            return [v]

        def g_tostring(v=None):
            h = E.mm(v, "__tostring")
            if h is not None:
                r = E.call_first(h, [v])
                if type(r) is not str:
                    raise LuaErr("input:1: '__tostring' must return a string")
                return [r]
            return [tostr(v)]

        def g_pairs(t=None):
            if type(t) is not Obj:
                raise LuaErr("input:1: bad argument #1 to 'for iterator' (table expected, got %s)" % lt(t))
            return [E.builtins["next"], t, None]

        def ip_step(t, i):
            i = int(i) + 1
            v = E.index(t, i)
            return [i, v] if v is not None else [None]

        ip_iter = L("ipairs_iter", ip_step)

        def g_ipairs(t=None):
            if type(t) is not Obj:
                raise LuaErr("input:1: bad argument #1 to 'ipairs' (table expected, got %s)" % lt(t))
            return [ip_iter, t, 0]

        def g_tonumber(*a):
            return [tonum(a[0], a[1] if len(a) > 1 else None)]

        def g_rawequal(a, b):
            return [a is b if type(a) is Obj or type(b) is Obj else (a == b and type(a) is type(b))]

        def g_rawget(t, k):
            return [t.d.get(nk(k))]

        def g_rawset(t, k, v):
            t.d[nk(k)] = v
            return [t]

        G = {
            "pcall": g_pcall, "error": g_error, "select": g_select, "setmetatable": g_setmt, "getmetatable": g_getmt,
            "next": g_next, "assert": g_assert, "type": lambda v=None: [lt(v)], "tostring": g_tostring,
            "tonumber": g_tonumber, "rawequal": g_rawequal, "rawget": g_rawget, "rawset": g_rawset, "unpack": t_unpack,
            "rawlen": lambda v: [E.length(v)], "pairs": g_pairs, "ipairs": g_ipairs,
        }
        self.builtins = {}
        for n, f in G.items():
            self.builtins[n] = L(n, f, "heap" if n in ("setmetatable", "rawset") else "pure")
        self.builtins["print"] = L("print", None, "impure")
        for lib in ("string", "table", "math"):
            for n, v in self.lib[lib].d.items():
                if type(v) is Bi:
                    self.builtins[lib + "." + n] = v
        self.builtins["string"] = string
        self.builtins["table"] = table
        self.builtins["math"] = mathlib
        for n, v in self.builtins.items():
            if type(v) is Bi and n in G:
                self.G.d[n] = v
        self.G.d["string"] = string
        self.G.d["table"] = table
        self.G.d["math"] = mathlib
        self.G.d["print"] = self.builtins["print"]
        self.G.d["_G"] = self.G

    def length(self, v):
        if type(v) is str:
            return len(v)
        if type(v) is Obj:
            if v.mt is not None and "__len" in v.mt.d:
                r = self.call(v.mt.d["__len"], [v])
                return r[0] if r else None
            n = 0
            while (n + 1) in v.d:
                n += 1
            return n
        raise LuaErr("input:1: attempt to get length of a %s value" % lt(v))

    def index(self, o, k):
        if is_sym(o) and not is_sym(k) and k is not None:
            self.escape(k)
            s = self.new_sym("v")
            self.res.append(("index", s, o, k))
            return s
        if is_sym(o) or is_sym(k):
            raise Stuck("symbolic index")
        if type(o) is str:
            return self.string.d.get(nk(k))
        if type(o) is Obj:
            if o.escaped:
                raise Stuck("escaped read")
            v = o.d.get(nk(k))
            if v is None and o.lib and type(k) is str and k in UNMODELED.get(o.lib, ()):
                raise Stuck("unmodeled library member %s.%s" % (o.lib, k))
            if v is None and o.mt is not None:
                h = o.mt.d.get("__index")
                if type(h) is Obj:
                    return self.index(h, k)
                if h is not None:
                    r = self.call(h, [o, k])
                    return r[0] if r else None
            return v
        raise LuaErr("input:1: attempt to index a %s value" % lt(o))

    def setindex(self, o, k, v):
        if type(o) is not Obj:
            raise LuaErr("input:1: attempt to index a %s value" % lt(o))
        if k is None:
            raise LuaErr("input:1: table index is nil")
        if o.mt is not None and nk(k) not in o.d:
            h = o.mt.d.get("__newindex")
            if h is not None:
                if type(h) is Obj:
                    return self.setindex(h, k, v)
                self.call(h, [o, k, v])
                return
        if v is None:
            o.d.pop(nk(k), None)
        else:
            o.d[nk(k)] = v

    ARITH_EVENTS = {"+": "__add", "-": "__sub", "*": "__mul", "/": "__div", "%": "__mod", "^": "__pow"}

    def mm(self, v, name):
        if type(v) is Obj and v.mt is not None:
            return v.mt.d.get(name)
        return None

    def mm_pair(self, a, b, name):
        h = self.mm(a, name)
        if h is None:
            h = self.mm(b, name)
        return h

    def call_first(self, f, args):
        r = self.call(f, args)
        return r[0] if r else None

    def arith(self, op, a, b):
        x = tonum(a) if type(a) is str else a
        y = tonum(b) if type(b) is str else b
        if not isnum(x) or not isnum(y):
            h = self.mm_pair(a, b, self.ARITH_EVENTS.get(op, ""))
            if h is not None:
                return self.call_first(h, [a, b])
            bad = a if not isnum(x) else b
            raise LuaErr("input:1: attempt to perform arithmetic on a %s value" % lt(bad))
        x = float(x)
        y = float(y)
        try:
            if op == "+":
                r = x + y
            elif op == "-":
                r = x - y
            elif op == "*":
                r = x * y
            elif op == "/":
                r = x / y if y != 0 else (INF if x > 0 else (-INF if x < 0 else float("nan")))
            elif op == "%":
                r = float("nan") if y == 0 else x % y
            else:
                r = x ** y
                if type(r) is complex:
                    r = float("nan")
        except OverflowError:
            r = INF
        except ZeroDivisionError:
            r = INF
        return num_norm(r)

    def binop(self, op, a, b):
        if is_sym(a) or is_sym(b):
            return self.sym_expr(("bin", op, a, b))
        if op in ARITH:
            return self.arith(op, a, b)
        if op == "..":
            for v in (a, b):
                if type(v) not in (str, int, float) or type(v) is bool:
                    h = self.mm_pair(a, b, "__concat")
                    if h is not None:
                        return self.call_first(h, [a, b])
                    raise LuaErr("input:1: attempt to concatenate a %s value" % lt(v))
            return tostr(a) + tostr(b)
        if op in ("==", "~="):
            if type(a) is bool or type(b) is bool or a is None or b is None:
                eq = a is b
            elif isnum(a) and isnum(b):
                eq = a == b
            elif type(a) is str and type(b) is str:
                eq = a == b
            elif a is b:
                eq = True
            elif type(a) is Obj and type(b) is Obj:
                h = self.mm_pair(a, b, "__eq")
                eq = self.truth(self.call_first(h, [a, b])) if h is not None else False
            else:
                eq = False
            return eq if op == "==" else not eq
        if op in COMPARE:
            if (isnum(a) and isnum(b)) or (type(a) is str and type(b) is str):
                return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
            event = "__lt" if op in ("<", ">") else "__le"
            first, second = (a, b) if op in ("<", "<=") else (b, a)
            h = self.mm_pair(first, second, event)
            if h is not None:
                return self.truth(self.call_first(h, [first, second]))
            raise LuaErr("input:1: attempt to compare %s with %s" % (lt(a), lt(b)))
        raise Stuck("operator %s" % op)

    def unop(self, op, a):
        if is_sym(a):
            return self.sym_expr(("un", op, a))
        if op == "not":
            return a is None or a is False
        if op == "-":
            x = tonum(a) if type(a) is str else a
            if not isnum(x):
                h = self.mm(a, "__unm")
                if h is not None:
                    return self.call_first(h, [a, a])
                raise LuaErr("input:1: attempt to perform arithmetic on a %s value" % lt(a))
            return num_norm(-x)
        if op == "#":
            return self.length(a)
        raise Stuck("unary %s" % op)

    def sym_expr(self, e):
        key = None
        operands = e[2:] if e[0] in ("bin", "un") else ()
        if operands and all(is_sym(x) or isnum(x) for x in operands) and not any(type(x) is bool for x in operands):
            key = (e[0], e[1]) + tuple(id(x) if is_sym(x) else ("n", x) for x in operands)
            hit = self.expr_cache.get(key)
            if hit is not None:
                return hit
        s = self.new_sym("v")
        self.res.append(("expr", s, e))
        if key is not None:
            self.expr_cache[key] = s
        return s

    def truth(self, v):
        if is_sym(v):
            if v.known:
                return True
            raise Stuck("symbolic condition")
        return v is not None and v is not False

    def call(self, f, args):
        self.steps += 1
        if self.steps > self.step_limit:
            raise Stuck("step limit")
        t = type(f)
        if t is Clo:
            if self.depth > 60:
                raise Stuck("depth")
            self.depth += 1
            try:
                r = self.run_clo(f, args)
            finally:
                self.depth -= 1
            self.probe(f, args, r)
            return r
        if t is Bi:
            return self.call_bi(f, args)
        if is_sym(f):
            return self.residual_call(f, args)
        if f is None:
            raise LuaErr("input:1: attempt to call a nil value")
        h = self.mm(f, "__call")
        if h is not None:
            return self.call(h, [f] + list(args))
        raise LuaErr("input:1: attempt to call a %s value" % lt(f))

    def residual_call(self, f, args):
        s = self.new_sym("v")
        for a in args:
            self.escape(a)
        self.escape(f)
        self.res.append(("call", s, f, list(args)))
        return [s]

    def escape(self, v):
        if type(v) is Obj:
            if not v.escaped:
                self.reify(v)
        elif type(v) is Clo:
            self.reify_clo(v)

    def reify(self, o):
        if o.lib:
            return
        if o.escaped:
            return
        o.escaped = True
        o.name = "o%d" % o.oid
        items = []
        for k, v in list(o.d.items()):
            kk = k[1] if type(k) is tuple else k
            self.escape(v)
            items.append((kk, v))
        mt = o.mt
        if mt is not None:
            self.reify(mt)
        self.res.append(("obj", o, items, mt))

    def reify_clo(self, c):
        if c.name is not None:
            return
        c.name = "c%d_%d" % (c.key % 100000, len(self.res))
        for cp in c.caps:
            self.escape(cp)
        self.need_keys.append(c.key)
        self.res.append(("clo", c))

    def probe(self, f, args, r):
        if self.decoder is not None or len(args) != 2 or not r:
            return
        if type(args[0]) is str and isnum(args[1]) and r[0] == args[1] and f.caps and type(f.caps[0]) is Obj:
            cache = f.caps[0].d.get("v")
            if type(cache) is Obj and type(cache.d.get(nk(args[1]))) is str:
                self.decoder = f

    def decode(self, s, n):
        c = self.decoder
        r = self.call(c, [s, n])
        cache = c.caps[0].d.get("v")
        v = cache.d.get(nk(r[0])) if type(cache) is Obj else None
        return v if type(v) is str else None

    def call_bi(self, f, args):
        if f.kind == "impure":
            for a in args:
                self.escape(a)
            self.res.append(("call", None, f, list(args)))
            return []
        if any(is_sym(a) for a in args):
            if f.name in ("pcall", "select", "setmetatable", "type", "tostring"):
                pass
            return self.residual_call(f, args)
        if f.kind == "heap":
            for a in args:
                if type(a) is Obj and a.escaped:
                    return self.residual_call(f, args)
        try:
            r = f.fn(*args)
        except LuaErr:
            raise
        except (TypeError, ValueError, IndexError, AttributeError, OverflowError) as e:
            raise LuaErr("input:1: bad argument to '%s'" % f.name)
        if r is None:
            return self.residual_call(f, args)
        return r

    def ev(self, e, fr):
        k = e[0]
        if k == "const":
            return e[1]
        if k == "reg":
            return fr.regs.get(e[1])
        if k == "tmp":
            tid = e[1]
            if tid in fr.multi:
                m = fr.multi[tid]
                return m[0] if m else None
            return fr.tmps.get(tid)
        if k == "bin":
            return self.binop(e[1], self.ev(e[2], fr), self.ev(e[3], fr))
        if k == "un":
            return self.unop(e[1], self.ev(e[2], fr))
        if k == "and":
            a = self.ev(e[1], fr)
            if is_sym(a):
                return self.sym_expr(("and", a, self.ev(e[2], fr)))
            return self.ev(e[2], fr) if self.truth(a) else a
        if k == "or":
            a = self.ev(e[1], fr)
            if is_sym(a):
                return self.sym_expr(("or", a, self.ev(e[2], fr)))
            return a if self.truth(a) else self.ev(e[2], fr)
        if k == "builtin":
            return self.builtin_value(e[1])
        if k == "env":
            return self.G
        if k == "varargs":
            t = self.new_obj()
            return t
        if k == "helper":
            return Bi("helper", lambda *a: None)
        if k == "cap":
            return fr.up[e[1] - 1] if e[1] - 1 < len(fr.up) else None
        if k == "arg":
            return fr.args[e[1] - 1] if e[1] - 1 < len(fr.args) else None
        if k == "args":
            t = self.new_obj()
            for i, v in enumerate(fr.args, 1):
                if v is not None:
                    t.d[i] = v
            t.d["n"] = len(fr.args)
            return t
        if k == "vararg":
            return fr.args[0] if fr.args else None
        if k == "table":
            return self.ev_table(e, fr)
        if k == "first":
            m = self.evm(e[1], fr)
            return m[0] if m else None
        if k == "unpack":
            m = self.evm(e, fr)
            return m[0] if m else None
        if k == "select":
            m = self.evm(e[2], fr)
            return m[e[1] - 1] if len(m) >= e[1] else None
        if k == "multi":
            m = self.evm(e[1], fr)
            return m[0] if m else None
        if k == "cellref":
            return self.ev(e[1], fr)
        raise Stuck("expression %s" % k)

    def evm(self, e, fr):
        k = e[0]
        if k == "tmp" and e[1] in fr.multi:
            return list(fr.multi[e[1]])
        if k == "multi":
            return self.evm(e[1], fr)
        if k == "unpack":
            t = self.ev(e[1], fr)
            if is_sym(t):
                raise Stuck("symbolic unpack")
            return self.builtins["table.unpack"].fn(t)
        if k == "vararg":
            return list(fr.args)
        return [self.ev(e, fr)]

    def evargs(self, lst, fr):
        out = []
        for i, a in enumerate(lst):
            if a[0] == "multi" and i == len(lst) - 1:
                out.extend(self.evm(a, fr))
            else:
                out.append(self.ev(a, fr))
        return out

    def ev_table(self, e, fr):
        t = self.new_obj()
        n = 0
        items = e[1]
        for i, it in enumerate(items):
            if it[0] == "pos":
                x = it[1]
                if x[0] == "multi" and i == len(items) - 1:
                    for v in self.evm(x, fr):
                        n += 1
                        if v is not None:
                            t.d[n] = v
                else:
                    n += 1
                    v = self.ev(x, fr)
                    if v is not None:
                        t.d[n] = v
            else:
                k = self.ev(it[1], fr)
                v = self.ev(it[2], fr)
                if is_sym(k):
                    raise Stuck("symbolic table key")
                if v is not None:
                    t.d[nk(k)] = v
        return t

    def run_clo(self, clo, args):
        an = self.an
        fr = Frame(clo.caps, list(args))
        for reg, expr in an.prologue:
            fr.regs[reg] = self.ev(self.prologue_expr(expr), fr)
        state = clo.key
        while True:
            r = an.lift(state)
            res = self.exec_state(r, fr)
            if res[0] == "ret":
                return res[1]
            state = res[1]

    def prologue_expr(self, e):
        if e[0] == "index":
            return ("const", self.index_value_static(e))
        if e[0] in ("or", "and"):
            return (e[0], self.prologue_expr(e[1]), self.prologue_expr(e[2]))
        return e

    def index_value_static(self, e):
        base = e[1]
        lib = self.builtin_value(base[1]) if base[0] == "builtin" else None
        if type(lib) is Obj:
            return lib.d.get(e[2][1])
        return None

    def builtin_value(self, name):
        v = self.builtins.get(name)
        if v is not None:
            return v
        if "." in name:
            lib, _, fn = name.partition(".")
            o = self.builtins.get(lib)
            if type(o) is Obj:
                return o.d.get(fn)
        s = self.syms_cache.get(name) if hasattr(self, "syms_cache") else None
        if s is None:
            if not hasattr(self, "syms_cache"):
                self.syms_cache = {}
            s = Sym(name)
            self.syms_cache[name] = s
        return s

    def exec_state(self, r, fr):
        fr.tmps = {}
        fr.multi = {}
        for s in r["stmts"]:
            k = s[0]
            if k == "let":
                self.exec_let(s, fr)
            elif k == "cellset":
                c = self.ev(s[1], fr)
                v = self.ev(s[2], fr)
                if type(c) is not Obj:
                    raise Stuck("cell target")
                if c.escaped:
                    self.res.append(("setindex", c, "v", v))
                    self.escape(v)
                else:
                    c.d["v"] = v
                    if v is None:
                        c.d.pop("v", None)
            elif k == "setglobal":
                key = self.ev(s[1], fr)
                v = self.ev(s[2], fr)
                if is_sym(key):
                    raise Stuck("symbolic global key")
                self.escape(v)
                self.G.d[nk(key)] = v
                self.res.append(("setglobal", key, v))
            elif k == "setindex":
                o = self.ev(s[1], fr)
                key = self.ev(s[2], fr)
                v = self.ev(s[3], fr)
                if is_sym(o) or is_sym(key):
                    self.escape(v)
                    self.res.append(("setindex", o, key, v))
                elif type(o) is Obj and o.escaped:
                    self.escape(v)
                    self.res.append(("setindex", o, key, v))
                else:
                    self.setindex(o, key, v)
        nx = r["next"]
        if nx[0] == "ret":
            rt = r["ret"]
            vals = []
            if rt is not None and rt[0] == "table":
                items = rt[1]
                for i, it in enumerate(items):
                    if it[0] != "pos":
                        continue
                    x = it[1]
                    if x[0] == "multi" and i == len(items) - 1:
                        vals.extend(self.evm(x, fr))
                    else:
                        vals.append(self.ev(x, fr))
            return ("ret", vals)
        cond = None
        if nx[0] == "branch":
            cond = self.ev(nx[1], fr)
        newregs = {}
        skip = (self.an.disp["state"], self.an.disp["ret"])
        for n, v in r["writes"].items():
            if n in skip:
                continue
            newregs[n] = self.ev(v, fr)
        for n, v in newregs.items():
            fr.regs[n] = v
        if nx[0] == "goto":
            return ("goto", nx[1])
        t = nx[2] if self.truth(cond) else nx[3]
        if t[0] == "goto":
            return ("goto", t[1])
        return ("ret", [])

    def exec_let(self, s, fr):
        tid = s[1]
        e = s[2]
        k = e[0]
        if k == "call":
            f = self.ev(e[1], fr)
            args = self.evargs(e[2], fr)
            fr.multi[tid] = self.call(f, args)
        elif k == "index":
            o = self.ev(e[1], fr)
            key = self.ev(e[2], fr)
            fr.tmps[tid] = self.index(o, key)
        elif k == "global":
            key = self.ev(e[1], fr)
            if is_sym(key):
                raise Stuck("symbolic global")
            v = self.G.d.get(nk(key))
            if v is None and nk(key) not in self.G.d:
                sy = self.new_sym("g")
                sy.known = type(key) is str and key in self.assume
                self.res.append(("global", sy, key))
                v = sy
            fr.tmps[tid] = v
        elif k == "cellval":
            c = self.ev(e[1], fr)
            if type(c) is not Obj:
                raise Stuck("cell read")
            if c.escaped:
                sy = self.new_sym("v")
                self.res.append(("index", sy, c, "v"))
                fr.tmps[tid] = sy
            else:
                fr.tmps[tid] = c.d.get("v")
        elif k == "alloc":
            fr.tmps[tid] = self.new_obj()
        elif k == "table":
            fr.tmps[tid] = self.ev_table(e, fr)
        elif k == "closure":
            caps = [self.ev(c, fr) for c in e[2]]
            fr.tmps[tid] = Clo(e[1], caps, e[3])
        else:
            raise Stuck("let %s" % k)

    def snapshot(self, fr):
        return copy.deepcopy((fr.regs, self.G, self.lib, self.builtins, self.string, self.decoder, self.decoded, self.oid, self.symn, len(self.res), len(self.need_keys), self.steps))

    def restore(self, fr, snap):
        (fr.regs, self.G, self.lib, self.builtins, self.string, self.decoder, self.decoded, self.oid, self.symn, nres, nkeys, self.steps) = snap
        del self.res[nres:]
        del self.need_keys[nkeys:]
        self.expr_cache.clear()

    def run_main(self, entry):
        an = self.an
        fr = Frame([], [])
        for reg, expr in an.prologue:
            fr.regs[reg] = self.ev(self.prologue_expr(expr), fr)
        state = entry
        self.stuck_reason = None
        while True:
            snap = self.snapshot(fr)
            try:
                r = an.lift(state)
                res = self.exec_state(r, fr)
            except (Stuck, LuaErr, RecursionError) as ex:
                self.restore(fr, snap)
                self.stuck_reason = ex.value if isinstance(ex, LuaErr) else str(ex)
                return ("stuck", state, fr)
            if res[0] == "ret":
                return ("ret", res[1], fr)
            state = res[1]
