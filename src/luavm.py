import re

from .lexer import tokenize
from .parser import LuaParser

INF = float("inf")
NAN = float("nan")
MAX_SAFE = 2 ** 53
TRUE_KEY = object()
FALSE_KEY = object()
CHUNK = "input"


class LuaError(Exception):
    def __init__(self, value):
        Exception.__init__(self, value)
        self.value = value


class LuaTable:
    __slots__ = ("arr", "hash", "meta", "snap", "pos")

    def __init__(self):
        self.arr = []
        self.hash = {}
        self.meta = None
        self.snap = None
        self.pos = None


class LuaFunction:
    __slots__ = ("fn", "name", "native")

    def __init__(self, fn, name="?", native=False):
        self.fn = fn
        self.name = name
        self.native = native


class LuaUserdata:
    __slots__ = ("meta",)

    def __init__(self):
        self.meta = None


class StepLimit(Exception):
    pass


BUDGET = [50000000]


def set_budget(n):
    BUDGET[0] = n


class Ret:
    __slots__ = ("vals",)

    def __init__(self, vals):
        self.vals = vals


class Brk:
    pass


BREAK = Brk()


class Frame:
    __slots__ = ("s", "u", "v")

    def __init__(self, s, u, v):
        self.s = s
        self.u = u
        self.v = v


STRING_META = LuaTable()


def rt_error(msg):
    return LuaError("%s:1: %s" % (CHUNK, msg))


def norm(v):
    if type(v) is float and v == v and -MAX_SAFE < v < MAX_SAFE and v == int(v):
        return int(v)
    return v


def num_result(r):
    if type(r) is int:
        if -MAX_SAFE < r < MAX_SAFE:
            return r
        return float(r)
    return norm(r)


def fmt_num(n):
    if type(n) is int:
        if -10 ** 14 < n < 10 ** 14:
            return str(n)
        n = float(n)
    if n != n:
        return "nan"
    if n == INF:
        return "inf"
    if n == -INF:
        return "-inf"
    return "%.14g" % n


NUM_RE = re.compile(r"\s*([+-]?)(0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*$")


def str_to_number(s):
    m = NUM_RE.match(s)
    if not m:
        return None
    sign, body = m.groups()
    if body[:2] in ("0x", "0X"):
        v = int(body, 16)
    elif body.isdigit():
        v = int(body)
    else:
        v = float(body)
    if sign == "-":
        v = -v
    return num_result(v)


def lua_type(v):
    if v is None:
        return "nil"
    t = type(v)
    if t is bool:
        return "boolean"
    if t is int or t is float:
        return "number"
    if t is str:
        return "string"
    if t is LuaTable:
        return "table"
    if t is LuaFunction:
        return "function"
    return "userdata"


def truthy(v):
    return v is not None and v is not False


def to_num(v):
    t = type(v)
    if t is int or t is float:
        return v
    if t is str:
        return str_to_number(v)
    return None


def to_str(v):
    t = type(v)
    if t is str:
        return v
    if t is int or t is float:
        return fmt_num(v)
    return None


def norm_key(k):
    t = type(k)
    if t is float:
        if k != k:
            raise rt_error("table index is NaN")
        if -MAX_SAFE < k < MAX_SAFE and k == int(k):
            return int(k)
        return k
    if t is bool:
        return TRUE_KEY if k else FALSE_KEY
    if k is None:
        raise rt_error("table index is nil")
    return k


def rawget(t, k):
    tk = type(k)
    if tk is int:
        a = t.arr
        if 0 < k <= len(a):
            return a[k - 1]
        return t.hash.get(k)
    if tk is float:
        if k != k:
            return None
        if -MAX_SAFE < k < MAX_SAFE and k == int(k):
            return rawget(t, int(k))
        return t.hash.get(k)
    if tk is bool:
        return t.hash.get(TRUE_KEY if k else FALSE_KEY)
    if k is None:
        return None
    return t.hash.get(k)


def rawset(t, k, v):
    k = norm_key(k)
    if type(k) is int:
        a = t.arr
        n = len(a)
        if 0 < k <= n:
            if v is None and k == n:
                a.pop()
                while a and a[-1] is None:
                    a.pop()
            else:
                a[k - 1] = v
            return
        if k == n + 1:
            if v is not None:
                a.append(v)
                h = t.hash
                if h:
                    k2 = k + 1
                    while k2 in h:
                        a.append(h.pop(k2))
                        k2 += 1
            else:
                t.hash.pop(k, None)
            return
    if v is None:
        t.hash.pop(k, None)
    else:
        t.hash[k] = v


def key_out(k):
    if k is TRUE_KEY:
        return True
    if k is FALSE_KEY:
        return False
    return k


def table_keys(t):
    keys = [i for i, v in enumerate(t.arr, 1) if v is not None]
    keys.extend(t.hash.keys())
    return keys


def table_next(t, k):
    if k is None:
        t.snap = table_keys(t)
        t.pos = {key: i for i, key in enumerate(t.snap)}
        start = 0
    else:
        nk = norm_key(k)
        if t.snap is None or nk not in t.pos:
            t.snap = table_keys(t)
            t.pos = {key: i for i, key in enumerate(t.snap)}
        if nk not in t.pos:
            return None, None
        start = t.pos[nk] + 1
    snap = t.snap
    while start < len(snap):
        key = snap[start]
        v = rawget(t, key_out(key))
        if v is not None:
            return key_out(key), v
        start += 1
    return None, None


def get_meta(obj):
    t = type(obj)
    if t is LuaTable or t is LuaUserdata:
        return obj.meta
    if t is str:
        return STRING_META
    return None


def get_mm(obj, name):
    mt = get_meta(obj)
    if mt is None:
        return None
    return mt.hash.get(name)


def first(r):
    return r[0] if r else None


def call(f, args):
    if type(f) is LuaFunction:
        return f.fn(args)
    mm = get_mm(f, "__call")
    if mm is not None:
        return call(mm, [f] + list(args))
    if hasattr(f, "sym_call"):
        return f.sym_call(args)
    raise rt_error("attempt to call a %s value" % lua_type(f))


def index(obj, key):
    t = type(obj)
    if t is LuaTable:
        v = rawget(obj, key)
        if v is not None:
            return v
        mt = obj.meta
        if mt is None:
            return None
        h = mt.hash.get("__index")
        if h is None:
            return None
        if type(h) is LuaFunction:
            return first(h.fn([obj, key]))
        return index(h, key)
    if t is str:
        h = STRING_META.hash.get("__index")
        if h is None:
            return None
        return index(h, key)
    if t is LuaUserdata:
        mt = obj.meta
        h = mt.hash.get("__index") if mt is not None else None
        if h is None:
            raise rt_error("attempt to index a userdata value")
        if type(h) is LuaFunction:
            return first(h.fn([obj, key]))
        return index(h, key)
    if hasattr(obj, "sym_index"):
        return obj.sym_index(key)
    raise rt_error("attempt to index a %s value" % lua_type(obj))


def setindex(obj, key, val):
    t = type(obj)
    if t is LuaTable:
        mt = obj.meta
        if mt is None:
            rawset(obj, key, val)
            return
        h = mt.hash.get("__newindex")
        if h is None or rawget(obj, key) is not None:
            rawset(obj, key, val)
            return
        if type(h) is LuaFunction:
            h.fn([obj, key, val])
        else:
            setindex(h, key, val)
        return
    if t is LuaUserdata:
        mt = obj.meta
        h = mt.hash.get("__newindex") if mt is not None else None
        if h is None:
            raise rt_error("attempt to index a userdata value")
        if type(h) is LuaFunction:
            h.fn([obj, key, val])
        else:
            setindex(h, key, val)
        return
    if hasattr(obj, "sym_setindex"):
        obj.sym_setindex(key, val)
        return
    raise rt_error("attempt to index a %s value" % lua_type(obj))


ARITH_MM = {"add": "__add", "sub": "__sub", "mul": "__mul", "div": "__div", "mod": "__mod", "pow": "__pow"}


def arith_slow(op, a, b):
    na = to_num(a)
    nb = to_num(b)
    if na is not None and nb is not None and type(a) is not LuaTable and type(b) is not LuaTable:
        return ARITH_FN[op](na, nb)
    mm = get_mm(a, ARITH_MM[op])
    if mm is None:
        mm = get_mm(b, ARITH_MM[op])
    if mm is not None:
        return first(call(mm, [a, b]))
    bad = b if na is not None else a
    raise rt_error("attempt to perform arithmetic on a %s value" % lua_type(bad))


def op_add(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is int or ta is float) and (tb is int or tb is float):
        r = a + b
        if type(r) is int:
            if -MAX_SAFE < r < MAX_SAFE:
                return r
            return float(r)
        return norm(r)
    return arith_slow("add", a, b)


def op_sub(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is int or ta is float) and (tb is int or tb is float):
        r = a - b
        if type(r) is int:
            if -MAX_SAFE < r < MAX_SAFE:
                return r
            return float(r)
        return norm(r)
    return arith_slow("sub", a, b)


def op_mul(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is int or ta is float) and (tb is int or tb is float):
        r = a * b
        if type(r) is int:
            if -MAX_SAFE < r < MAX_SAFE:
                return r
            return float(r)
        return norm(r)
    return arith_slow("mul", a, b)


def div_num(a, b):
    if b == 0:
        if a == 0 or a != a:
            return NAN
        neg = (a < 0) != (str(float(b))[0] == "-")
        return -INF if neg else INF
    return norm(a / b)


def op_div(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is int or ta is float) and (tb is int or tb is float):
        return div_num(a, b)
    return arith_slow("div", a, b)


def mod_num(a, b):
    if b == 0:
        return NAN
    if a in (INF, -INF) or a != a or b != b:
        return NAN
    if b in (INF, -INF):
        if (a >= 0) == (b > 0):
            return a
        return b
    return num_result(a % b)


def op_mod(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is int or ta is float) and (tb is int or tb is float):
        return mod_num(a, b)
    return arith_slow("mod", a, b)


def pow_num(a, b):
    try:
        return norm(float(a) ** b)
    except OverflowError:
        return INF
    except ZeroDivisionError:
        return INF
    except ValueError:
        return NAN


def op_pow(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is int or ta is float) and (tb is int or tb is float):
        r = pow_num(a, b)
        if type(r) is complex:
            return NAN
        return r
    return arith_slow("pow", a, b)


ARITH_FN = {
    "add": op_add,
    "sub": op_sub,
    "mul": op_mul,
    "div": op_div,
    "mod": op_mod,
    "pow": op_pow,
}


def op_neg(a):
    t = type(a)
    if t is int or t is float:
        return -a
    n = to_num(a) if t is str else None
    if n is not None:
        return -n
    mm = get_mm(a, "__unm")
    if mm is not None:
        return first(call(mm, [a, a]))
    raise rt_error("attempt to perform arithmetic on a %s value" % lua_type(a))


def op_len(v):
    t = type(v)
    if t is str:
        return len(v)
    if t is LuaTable:
        mt = v.meta
        if mt is not None:
            h = mt.hash.get("__len")
            if h is not None:
                return first(call(h, [v]))
        return len(v.arr)
    mm = get_mm(v, "__len")
    if mm is not None:
        return first(call(mm, [v]))
    raise rt_error("attempt to get length of a %s value" % lua_type(v))


def op_concat(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is str or ta is int or ta is float) and (tb is str or tb is int or tb is float):
        return (a if ta is str else fmt_num(a)) + (b if tb is str else fmt_num(b))
    mm = get_mm(a, "__concat")
    if mm is None:
        mm = get_mm(b, "__concat")
    if mm is not None:
        return first(call(mm, [a, b]))
    bad = b if (ta is str or ta is int or ta is float) else a
    raise rt_error("attempt to concatenate a %s value" % lua_type(bad))


def lua_eq(a, b):
    if a is b:
        return not (type(a) is float and a != a)
    ta = type(a)
    tb = type(b)
    if ta is bool or tb is bool:
        return False
    if (ta is int or ta is float) and (tb is int or tb is float):
        return a == b
    if ta is str and tb is str:
        return a == b
    if (ta is LuaTable and tb is LuaTable) or (ta is LuaUserdata and tb is LuaUserdata):
        m1 = get_mm(a, "__eq")
        if m1 is not None and m1 is get_mm(b, "__eq"):
            return truthy(first(call(m1, [a, b])))
    return False


def cmp_error(a, b):
    t1 = lua_type(a)
    t2 = lua_type(b)
    if t1 == t2:
        return rt_error("attempt to compare two %s values" % t1)
    return rt_error("attempt to compare %s with %s" % (t1, t2))


def op_lt(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is int or ta is float) and (tb is int or tb is float):
        return a < b
    if ta is str and tb is str:
        return a < b
    mm = get_mm(a, "__lt")
    if mm is None:
        mm = get_mm(b, "__lt")
    if mm is not None:
        return truthy(first(call(mm, [a, b])))
    raise cmp_error(a, b)


def op_le(a, b):
    ta = type(a)
    tb = type(b)
    if (ta is int or ta is float) and (tb is int or tb is float):
        return a <= b
    if ta is str and tb is str:
        return a <= b
    mm = get_mm(a, "__le")
    if mm is None:
        mm = get_mm(b, "__le")
    if mm is not None:
        return truthy(first(call(mm, [a, b])))
    mm = get_mm(a, "__lt")
    if mm is None:
        mm = get_mm(b, "__lt")
    if mm is not None:
        return not truthy(first(call(mm, [b, a])))
    raise cmp_error(a, b)


BINOPS = {
    "+": op_add,
    "-": op_sub,
    "*": op_mul,
    "/": op_div,
    "%": op_mod,
    "^": op_pow,
    "..": op_concat,
    "==": lua_eq,
    "~=": lambda a, b: not lua_eq(a, b),
    "<": op_lt,
    "<=": op_le,
    ">": lambda a, b: op_lt(b, a),
    ">=": lambda a, b: op_le(b, a),
}


class FuncState:
    def __init__(self, parent):
        self.parent = parent
        self.scopes = [{}]
        self.nslots = 0
        self.upvals = []
        self.upmap = {}

    def declare(self, name):
        slot = self.nslots
        self.nslots += 1
        self.scopes[-1][name] = slot
        return slot

    def push(self):
        self.scopes.append({})

    def pop(self):
        self.scopes.pop()


def is_multi(node):
    return node[0] in ("callx", "method", "vararg")


def describe(node, depth=0):
    if depth > 3:
        return "..."
    k = node[0]
    if k == "const":
        return repr(node[1])
    if k == "name":
        return node[1]
    if k == "index":
        return "%s[%s]" % (describe(node[1], depth + 1), describe(node[2], depth + 1))
    if k == "bin":
        return "(%s %s %s)" % (describe(node[2], depth + 1), node[1], describe(node[3], depth + 1))
    if k == "callx":
        return "%s(...)" % describe(node[1], depth + 1)
    if k == "method":
        return "%s:%s(...)" % (describe(node[1], depth + 1), node[2])
    if k == "paren":
        return "(%s)" % describe(node[1], depth + 1)
    if k == "un":
        return "%s%s" % (node[1], describe(node[2], depth + 1))
    return k


def annotate(fn, node):
    def run(f):
        try:
            return fn(f)
        except LuaError as e:
            if isinstance(e.value, str) and " [at " not in e.value:
                raise LuaError("%s [at %s]" % (e.value, describe(node)))
            raise

    return run


class Compiler:
    def __init__(self, globals_table, debug=False):
        self.G = globals_table
        self.debug = debug

    def resolve(self, fs, name):
        for scope in reversed(fs.scopes):
            if name in scope:
                return ("l", scope[name])
        if fs.parent is None:
            return ("g", name)
        r = self.resolve(fs.parent, name)
        if r[0] == "g":
            return r
        if r not in fs.upmap:
            fs.upmap[r] = len(fs.upvals)
            fs.upvals.append(r)
        return ("u", fs.upmap[r])

    def compile_main(self, stmts):
        fs = FuncState(None)
        code = self.block(stmts, fs, False)
        return fs, code

    def make_main(self, stmts):
        fs, code = self.compile_main(stmts)

        def fn(args):
            fr = Frame([None] * fs.nslots, [], list(args))
            r = code(fr)
            if r is None or r is BREAK:
                return []
            return r.vals

        return LuaFunction(fn, "main")

    def block(self, stmts, fs, new_scope=True):
        if new_scope:
            fs.push()
        code = [self.stmt(s, fs) for s in stmts]
        if new_scope:
            fs.pop()
        n = len(code)
        if n == 0:
            return lambda f: None
        if n == 1:
            return code[0]
        if n == 2:
            c0, c1 = code

            def run2(f):
                r = c0(f)
                if r is not None:
                    return r
                return c1(f)

            return run2

        def run(f):
            for c in code:
                r = c(f)
                if r is not None:
                    return r
            return None

        return run

    def explist(self, exprs, fs):
        if not exprs:
            return lambda f: []
        singles = [self.expr(e, fs) for e in exprs[:-1]]
        last = exprs[-1]
        if is_multi(last):
            lastm = self.multi(last, fs)
            if not singles:
                return lambda f: list(lastm(f))

            def run_m(f):
                out = [c(f) for c in singles]
                out.extend(lastm(f))
                return out

            return run_m
        lasts = self.expr(last, fs)
        singles.append(lasts)
        if len(singles) == 1:
            s0 = singles[0]
            return lambda f: [s0(f)]
        if len(singles) == 2:
            s0, s1 = singles
            return lambda f: [s0(f), s1(f)]
        return lambda f: [c(f) for c in singles]

    def multi(self, node, fs):
        kind = node[0]
        if kind == "vararg":
            return lambda f: list(f.v) if f.v else []
        if kind == "callx":
            fn = self.expr(node[1], fs)
            args = self.explist(node[2], fs)

            def run_call(f):
                fv = fn(f)
                a = args(f)
                if type(fv) is LuaFunction:
                    return fv.fn(a)
                return call(fv, a)

            return run_call
        if kind == "method":
            obj = self.expr(node[1], fs)
            name = node[2]
            args = self.explist(node[3], fs)

            def run_method(f):
                o = obj(f)
                fv = index(o, name)
                a = args(f)
                a.insert(0, o)
                if type(fv) is LuaFunction:
                    return fv.fn(a)
                return call(fv, a)

            return run_method
        e = self.expr(node, fs)
        return lambda f: [e(f)]

    def expr(self, node, fs):
        r = self.expr_inner(node, fs)
        if self.debug and node[0] in ("bin", "index", "callx", "method", "un"):
            return annotate(r, node)
        return r

    def expr_inner(self, node, fs):
        kind = node[0]
        if kind == "const":
            v = node[1]
            if type(v) is int and not (-MAX_SAFE < v < MAX_SAFE):
                v = float(v)
            return lambda f: v
        if kind == "name":
            r = self.resolve(fs, node[1])
            if r[0] == "l":
                i = r[1]
                return lambda f: f.s[i][0]
            if r[0] == "u":
                i = r[1]
                return lambda f: f.u[i][0]
            name = r[1]
            G = self.G

            def gget(f):
                v = G.hash.get(name)
                if v is None and G.meta is not None:
                    return index(G, name)
                return v

            return gget
        if kind == "paren":
            return self.expr(node[1], fs)
        if kind == "index":
            obj = self.expr(node[1], fs)
            key = self.expr(node[2], fs)
            if node[2][0] == "const" and type(node[2][1]) is str:
                k = node[2][1]

                def index_const(f):
                    o = obj(f)
                    if type(o) is LuaTable:
                        v = o.hash.get(k)
                        if v is not None:
                            return v
                    return index(o, k)

                return index_const
            return lambda f: index(obj(f), key(f))
        if kind == "callx" or kind == "method":
            m = self.multi(node, fs)

            def single(f):
                r = m(f)
                return r[0] if r else None

            return single
        if kind == "vararg":
            return lambda f: (f.v[0] if f.v else None)
        if kind == "func":
            return self.func(node, fs)
        if kind == "table":
            return self.table(node, fs)
        if kind == "and":
            a = self.expr(node[1], fs)
            b = self.expr(node[2], fs)

            def run_and(f):
                x = a(f)
                if x is None or x is False:
                    return x
                return b(f)

            return run_and
        if kind == "or":
            a = self.expr(node[1], fs)
            b = self.expr(node[2], fs)

            def run_or(f):
                x = a(f)
                if x is None or x is False:
                    return b(f)
                return x

            return run_or
        if kind == "un":
            op = node[1]
            x = self.expr(node[2], fs)
            if op == "not":
                return lambda f: not truthy(x(f))
            if op == "-":
                return lambda f: op_neg(x(f))
            return lambda f: op_len(x(f))
        if kind == "bin":
            op = node[1]
            a = self.expr(node[2], fs)
            b = self.expr(node[3], fs)
            fn = BINOPS[op]
            return lambda f: fn(a(f), b(f))
        raise SyntaxError("unsupported expression %r" % (kind,))

    def table(self, node, fs):
        items = node[1]
        compiled = []
        for i, it in enumerate(items):
            if it[0] == "pos":
                last = i == len(items) - 1
                if last and is_multi(it[1]):
                    compiled.append(("m", self.multi(it[1], fs)))
                else:
                    compiled.append(("p", self.expr(it[1], fs)))
            else:
                compiled.append(("k", self.expr(it[1], fs), self.expr(it[2], fs)))

        def build(f):
            t = LuaTable()
            n = 0
            for c in compiled:
                tag = c[0]
                if tag == "p":
                    n += 1
                    rawset(t, n, c[1](f))
                elif tag == "m":
                    for v in c[1](f):
                        n += 1
                        rawset(t, n, v)
                else:
                    rawset(t, c[1](f), c[2](f))
            return t

        return build

    def func(self, node, fs):
        _, params, vararg, body = node
        child = FuncState(fs)
        for p in params:
            child.declare(p)
        code = self.block(body, child, True)
        nparams = len(params)
        upsrc = child.upvals
        nslots = child.nslots

        def make(f):
            ups = [f.s[i] if k == "l" else f.u[i] for k, i in upsrc]

            def fn(args):
                b = BUDGET
                b[0] -= 1
                if b[0] < 0:
                    raise StepLimit()
                slots = [None] * nslots
                n = len(args)
                for i in range(nparams):
                    slots[i] = [args[i] if i < n else None]
                fr = Frame(slots, ups, args[nparams:] if vararg else None)
                r = code(fr)
                if r is None or r is BREAK:
                    return []
                return r.vals

            return LuaFunction(fn, "lua")

        return make

    def setter(self, target, fs):
        kind = target[0]
        if kind == "name":
            r = self.resolve(fs, target[1])
            if r[0] == "l":
                i = r[1]

                def set_local(f, v):
                    f.s[i][0] = v

                return None, None, set_local
            if r[0] == "u":
                i = r[1]

                def set_up(f, v):
                    f.u[i][0] = v

                return None, None, set_up
            name = r[1]
            G = self.G

            def set_global(f, v):
                if G.meta is None:
                    rawset(G, name, v)
                else:
                    setindex(G, name, v)

            return None, None, set_global
        if kind == "index":
            obj = self.expr(target[1], fs)
            key = self.expr(target[2], fs)

            def set_index(f, v, o=None, k=None):
                setindex(o, k, v)

            return obj, key, set_index
        raise SyntaxError("bad assignment target")

    def stmt(self, node, fs):
        kind = node[0]
        if kind == "local":
            return self.s_local(node, fs)
        if kind == "assign":
            return self.s_assign(node, fs)
        if kind == "call":
            m = self.multi(node[1], fs)

            def run_call(f):
                m(f)

            return run_call
        if kind == "if":
            return self.s_if(node, fs)
        if kind == "while":
            return self.s_while(node, fs)
        if kind == "repeat":
            return self.s_repeat(node, fs)
        if kind == "fornum":
            return self.s_fornum(node, fs)
        if kind == "forin":
            return self.s_forin(node, fs)
        if kind == "do":
            return self.block(node[1], fs, True)
        if kind == "break":
            return lambda f: BREAK
        if kind == "return":
            exprs = node[1]
            if len(exprs) == 1 and not is_multi(exprs[0]):
                e0 = self.expr(exprs[0], fs)
                return lambda f: Ret([e0(f)])
            lst = self.explist(exprs, fs)
            return lambda f: Ret(lst(f))
        if kind == "localfunc":
            slot = fs.declare(node[1])
            mk = self.func(node[2], fs)

            def run_lf(f):
                cell = [None]
                f.s[slot] = cell
                cell[0] = mk(f)

            return run_lf
        raise SyntaxError("unsupported statement %r" % (kind,))

    def s_local(self, node, fs):
        names, exprs = node[1], node[2]
        if len(names) == 1 and len(exprs) == 1:
            e0 = self.expr(exprs[0], fs)
            slot = fs.declare(names[0])

            def run1(f):
                f.s[slot] = [e0(f)]

            return run1
        if not exprs:
            slots = [fs.declare(n) for n in names]

            def run_nil(f):
                for s in slots:
                    f.s[s] = [None]

            return run_nil
        lst = self.explist(exprs, fs)
        slots = [fs.declare(n) for n in names]
        n = len(slots)

        def run_n(f):
            vals = lst(f)
            m = len(vals)
            for i in range(n):
                f.s[slots[i]] = [vals[i] if i < m else None]

        return run_n

    def s_assign(self, node, fs):
        targets, exprs = node[1], node[2]
        if len(targets) == 1 and len(exprs) == 1:
            obj, key, setfn = self.setter(targets[0], fs)
            e0 = self.expr(exprs[0], fs)
            if obj is None:

                def run_simple(f):
                    setfn(f, e0(f))

                return run_simple

            def run_idx(f):
                o = obj(f)
                k = key(f)
                setindex(o, k, e0(f))

            return run_idx
        setters = [self.setter(t, fs) for t in targets]
        lst = self.explist(exprs, fs)

        def run_multi(f):
            prepared = []
            for obj, key, setfn in setters:
                if obj is None:
                    prepared.append((False, None, None, setfn))
                else:
                    prepared.append((True, obj(f), key(f), setfn))
            vals = lst(f)
            m = len(vals)
            for i in range(len(prepared) - 1, -1, -1):
                is_idx, o, k, setfn = prepared[i]
                v = vals[i] if i < m else None
                if is_idx:
                    setindex(o, k, v)
                else:
                    setfn(f, v)

        return run_multi

    def s_if(self, node, fs):
        clauses = [(self.expr(c, fs), self.block(b, fs, True)) for c, b in node[1]]
        other = self.block(node[2], fs, True) if node[2] is not None else None
        if len(clauses) == 1:
            c0, b0 = clauses[0]
            if other is None:

                def run_if(f):
                    x = c0(f)
                    if x is not None and x is not False:
                        return b0(f)
                    return None

                return run_if

            def run_ifelse(f):
                x = c0(f)
                if x is not None and x is not False:
                    return b0(f)
                return other(f)

            return run_ifelse

        def run_chain(f):
            for c, b in clauses:
                x = c(f)
                if x is not None and x is not False:
                    return b(f)
            if other is not None:
                return other(f)
            return None

        return run_chain

    def s_while(self, node, fs):
        cond = self.expr(node[1], fs)
        body = self.block(node[2], fs, True)

        def run(f):
            b = BUDGET
            while truthy(cond(f)):
                b[0] -= 1
                if b[0] < 0:
                    raise StepLimit()
                r = body(f)
                if r is not None:
                    if r is BREAK:
                        break
                    return r
            return None

        return run

    def s_repeat(self, node, fs):
        fs.push()
        stmts = [self.stmt(s, fs) for s in node[1]]
        cond = self.expr(node[2], fs)
        fs.pop()

        def run(f):
            b = BUDGET
            while True:
                b[0] -= 1
                if b[0] < 0:
                    raise StepLimit()
                broke = False
                for c in stmts:
                    r = c(f)
                    if r is not None:
                        if r is BREAK:
                            broke = True
                            break
                        return r
                if broke:
                    return None
                if truthy(cond(f)):
                    return None

        return run

    def s_fornum(self, node, fs):
        _, name, a, b, c, body = node
        ea = self.expr(a, fs)
        eb = self.expr(b, fs)
        ec = self.expr(c, fs) if c is not None else None
        fs.push()
        slot = fs.declare(name)
        code = self.block(body, fs, True)
        fs.pop()

        def run(f):
            start = to_num(ea(f))
            stop = to_num(eb(f))
            step = to_num(ec(f)) if ec is not None else 1
            if start is None:
                raise rt_error("'for' initial value must be a number")
            if stop is None:
                raise rt_error("'for' limit must be a number")
            if step is None:
                raise rt_error("'for' step must be a number")
            slots = f.s
            if step == 0:
                raise rt_error("'for' step is zero")
            if type(start) is int and type(stop) is int and type(step) is int:
                rng = range(start, stop + (1 if step > 0 else -1), step)
                b = BUDGET
                for i in rng:
                    b[0] -= 1
                    if b[0] < 0:
                        raise StepLimit()
                    slots[slot] = [i]
                    r = code(f)
                    if r is not None:
                        if r is BREAK:
                            break
                        return r
                return None
            i = start
            b = BUDGET
            while (step > 0 and i <= stop) or (step <= 0 and i >= stop):
                b[0] -= 1
                if b[0] < 0:
                    raise StepLimit()
                slots[slot] = [norm(i)]
                r = code(f)
                if r is not None:
                    if r is BREAK:
                        break
                    return r
                i += step
            return None

        return run

    def s_forin(self, node, fs):
        _, names, exprs, body = node
        lst = self.explist(exprs, fs)
        fs.push()
        slots = [fs.declare(n) for n in names]
        code = self.block(body, fs, True)
        fs.pop()
        n = len(slots)

        def run(f):
            vals = lst(f)
            fn = vals[0] if vals else None
            state = vals[1] if len(vals) > 1 else None
            var = vals[2] if len(vals) > 2 else None
            slotlist = f.s
            b = BUDGET
            while True:
                b[0] -= 1
                if b[0] < 0:
                    raise StepLimit()
                rets = fn.fn([state, var]) if type(fn) is LuaFunction else call(fn, [state, var])
                if not rets or rets[0] is None:
                    return None
                var = rets[0]
                m = len(rets)
                for i in range(n):
                    slotlist[slots[i]] = [rets[i] if i < m else None]
                r = code(f)
                if r is not None:
                    if r is BREAK:
                        break
                    return r
            return None

        return run


def parse_chunk(src):
    toks = tokenize(src)
    p = LuaParser(toks)
    stmts = p.block()
    if p.peek() is not None:
        raise SyntaxError("unexpected token %r" % (p.peek(),))
    return stmts
