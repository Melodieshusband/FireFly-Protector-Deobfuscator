import math
import random
import re
import sys
import time
from functools import cmp_to_key

from . import luapattern as lp
from .luavm import (
    CHUNK,
    INF,
    NAN,
    STRING_META,
    Compiler,
    LuaError,
    LuaFunction,
    LuaTable,
    LuaUserdata,
    call,
    first,
    fmt_num,
    get_meta,
    get_mm,
    index,
    lua_eq,
    lua_type,
    norm,
    op_le,
    op_lt,
    op_len,
    parse_chunk,
    rawget,
    rawset,
    rt_error,
    setindex,
    str_to_number,
    to_num,
    to_str,
    truthy,
)


def bad_arg(i, fname, expected, got):
    return rt_error("bad argument #%d to '%s' (%s expected, got %s)" % (i + 1, fname, expected, got))


def arg_at(args, i):
    return args[i] if i < len(args) else None


def arg_str(args, i, fname):
    v = arg_at(args, i)
    s = to_str(v)
    if s is None:
        raise bad_arg(i, fname, "string", "no value" if i >= len(args) else lua_type(v))
    return s


def arg_num(args, i, fname, default=None):
    v = arg_at(args, i)
    if v is None and default is not None:
        return default
    n = to_num(v)
    if n is None:
        raise bad_arg(i, fname, "number", "no value" if i >= len(args) else lua_type(v))
    return n


def arg_int(args, i, fname, default=None):
    n = arg_num(args, i, fname, default)
    if n != n:
        return 0
    if n == INF:
        return 2 ** 62
    if n == -INF:
        return -(2 ** 62)
    return int(n)


def arg_table(args, i, fname):
    v = arg_at(args, i)
    if type(v) is not LuaTable:
        raise bad_arg(i, fname, "table", "no value" if i >= len(args) else lua_type(v))
    return v


def addr(v):
    return "0x%08x" % (id(v) & 0xFFFFFFFF)


def lua_tostring(v):
    t = type(v)
    if v is None:
        return "nil"
    if t is bool:
        return "true" if v else "false"
    if t is str:
        return v
    if t is int or t is float:
        return fmt_num(v)
    mm = get_mm(v, "__tostring")
    if mm is not None:
        r = first(call(mm, [v]))
        if type(r) is not str:
            raise rt_error("'__tostring' must return a string")
        return r
    if hasattr(v, "sym_tostring"):
        return v.sym_tostring()
    if t is LuaTable:
        return "table: " + addr(v)
    if t is LuaFunction:
        return "function: " + addr(v)
    return "userdata: " + addr(v)


def raw_eq(a, b):
    if a is b:
        return True
    ta = type(a)
    tb = type(b)
    if ta is bool or tb is bool:
        return False
    if (ta is int or ta is float) and (tb is int or tb is float):
        return a == b
    if ta is str and tb is str:
        return a == b
    return False


def posrelat(pos, length):
    if pos >= 0:
        return pos
    if -pos > length:
        return 0
    return length + pos + 1


FMT_RE = re.compile(r"%([-+ #0]*)(\d*)(?:\.(\d*))?([a-zA-Z%])")


def quote_string(s):
    out = ['"']
    for c in s:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\0":
            out.append("\\0")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def str_format(args):
    fmt = arg_str(args, 0, "format")
    out = []
    pos = 0
    argi = 1
    for m in FMT_RE.finditer(fmt):
        out.append(fmt[pos:m.start()])
        pos = m.end()
        flags, width, prec, spec = m.groups()
        if spec == "%":
            out.append("%")
            continue
        spec_text = "%" + flags + width + ("." + prec if prec is not None else "")
        if spec in "diuxXoc":
            n = arg_num(args, argi, "format")
            argi += 1
            n = int(n) if n == n and abs(n) != INF else 0
            if spec == "c":
                out.append(chr(n & 255))
            elif spec in "di" or spec == "u":
                out.append((spec_text + "d") % n)
            else:
                if n < 0:
                    n &= 0xFFFFFFFFFFFFFFFF
                out.append((spec_text + spec) % n)
        elif spec in "eEfFgG":
            n = arg_num(args, argi, "format")
            argi += 1
            out.append((spec_text + spec) % float(n))
        elif spec == "s":
            if argi >= len(args):
                raise bad_arg(argi, "format", "string", "no value")
            sv = lua_tostring(args[argi])
            argi += 1
            out.append((spec_text + "s") % sv)
        elif spec == "q":
            out.append(quote_string(arg_str(args, argi, "format")))
            argi += 1
        else:
            raise rt_error("invalid option '%%%s' to 'format'" % spec)
    out.append(fmt[pos:])
    return ["".join(out)]


def run_pattern(fn):
    try:
        return fn()
    except lp.PatternError as e:
        raise rt_error(str(e))


def str_find_aux(args, find):
    fname = "find" if find else "match"
    s = arg_str(args, 0, fname)
    pat = arg_str(args, 1, fname)
    init = posrelat(arg_int(args, 2, fname, 1), len(s)) - 1
    if init < 0:
        init = 0
    if init > len(s):
        return [None]
    if find and (truthy(arg_at(args, 3)) or not lp.has_specials(pat)):
        i = s.find(pat, init)
        if i < 0:
            return [None]
        return [i + 1, i + len(pat)]
    anchor = pat[:1] == "^"
    p0 = 1 if anchor else 0
    ms = lp.MatchState(s, pat)
    s1 = init

    def attempt():
        nonlocal s1
        while True:
            ms.reset()
            e = lp.do_match(ms, s1, p0)
            if e != -1:
                if find:
                    return [s1 + 1, e] + lp.get_captures(ms, s1, e, False)
                return lp.get_captures(ms, s1, e, True)
            s1 += 1
            if s1 > len(s) or anchor:
                return [None]

    return run_pattern(attempt)


def str_gmatch(args):
    s = arg_str(args, 0, "gmatch")
    pat = arg_str(args, 1, "gmatch")
    state = {"pos": 0}

    def iterator(_):
        ms = lp.MatchState(s, pat)

        def step():
            src = state["pos"]
            while src <= len(s):
                ms.reset()
                e = lp.do_match(ms, src, 0)
                if e != -1:
                    state["pos"] = e + 1 if e == src else e
                    return lp.get_captures(ms, src, e, True)
                src += 1
            state["pos"] = len(s) + 1
            return [None]

        return run_pattern(step)

    return [LuaFunction(iterator, "gmatch_iter", True)]


def add_replacement(ms, repl, s, e, out):
    tr = type(repl)
    whole = ms.src[s:e]
    if tr is str:
        i = 0
        n = len(repl)
        while i < n:
            c = repl[i]
            if c != "%":
                out.append(c)
                i += 1
                continue
            i += 1
            if i >= n:
                raise rt_error("invalid use of '%' in replacement string")
            d = repl[i]
            i += 1
            if d == "%":
                out.append("%")
            elif d.isdigit():
                if d == "0":
                    out.append(whole)
                else:
                    v = lp.get_capture(ms, int(d) - 1, s, e)
                    out.append(v if type(v) is str else fmt_num(v))
            else:
                raise rt_error("invalid use of '%' in replacement string")
        return
    caps = lp.get_captures(ms, s, e, True)
    if tr is LuaTable:
        val = index(repl, caps[0])
    elif tr is LuaFunction:
        val = first(repl.fn(caps))
    else:
        val = call(repl, caps)[0] if call(repl, caps) else None
    if val is None or val is False:
        out.append(whole)
    elif type(val) is str:
        out.append(val)
    elif type(val) in (int, float):
        out.append(fmt_num(val))
    else:
        raise rt_error("invalid replacement value (a %s)" % lua_type(val))


def str_gsub(args):
    s = arg_str(args, 0, "gsub")
    pat = arg_str(args, 1, "gsub")
    repl = arg_at(args, 2)
    tr = type(repl)
    if tr is int or tr is float:
        repl = fmt_num(repl)
        tr = str
    if tr is not str and tr is not LuaTable and tr is not LuaFunction:
        raise bad_arg(2, "gsub", "string/function/table", lua_type(repl))
    max_s = arg_int(args, 3, "gsub", len(s) + 1)
    anchor = pat[:1] == "^"
    p0 = 1 if anchor else 0
    ms = lp.MatchState(s, pat)
    out = []

    def work():
        src = 0
        n = 0
        while n < max_s:
            ms.reset()
            e = lp.do_match(ms, src, p0)
            if e != -1:
                n += 1
                add_replacement(ms, repl, src, e, out)
            if e != -1 and e > src:
                src = e
            elif src < len(s):
                out.append(s[src])
                src += 1
            else:
                break
            if anchor:
                break
        out.append(s[src:])
        return [("".join(out)), n]

    return run_pattern(work)


def build_string_lib(interp):
    lib = LuaTable()

    def reg(name):
        def deco(fn):
            rawset(lib, name, LuaFunction(fn, name, True))
            return fn

        return deco

    @reg("len")
    def s_len(args):
        return [len(arg_str(args, 0, "len"))]

    @reg("sub")
    def s_sub(args):
        s = arg_str(args, 0, "sub")
        n = len(s)
        i = posrelat(arg_int(args, 1, "sub", 1), n)
        j = arg_int(args, 2, "sub", -1)
        j = posrelat(j, n)
        if i < 1:
            i = 1
        if j > n:
            j = n
        if i > j:
            return [""]
        return [s[i - 1:j]]

    @reg("upper")
    def s_upper(args):
        return ["".join(c.upper() if "a" <= c <= "z" else c for c in arg_str(args, 0, "upper"))]

    @reg("lower")
    def s_lower(args):
        return ["".join(c.lower() if "A" <= c <= "Z" else c for c in arg_str(args, 0, "lower"))]

    @reg("rep")
    def s_rep(args):
        s = arg_str(args, 0, "rep")
        n = arg_int(args, 1, "rep")
        sep = to_str(arg_at(args, 2)) or ""
        if n <= 0:
            return [""]
        if (len(s) + len(sep)) * n > 64 * 1024 * 1024:
            raise rt_error("not enough memory")
        return [sep.join([s] * n)]

    @reg("reverse")
    def s_reverse(args):
        return [arg_str(args, 0, "reverse")[::-1]]

    @reg("byte")
    def s_byte(args):
        s = arg_str(args, 0, "byte")
        n = len(s)
        i = posrelat(arg_int(args, 1, "byte", 1), n)
        j = posrelat(arg_int(args, 2, "byte", i), n)
        if i < 1:
            i = 1
        if j > n:
            j = n
        return [ord(c) for c in s[i - 1:j]]

    @reg("char")
    def s_char(args):
        out = []
        for i in range(len(args)):
            c = arg_int(args, i, "char")
            if c < 0 or c > 255:
                raise bad_arg(i, "char", "number in range 0-255", "number")
            out.append(chr(c))
        return ["".join(out)]

    @reg("format")
    def s_format(args):
        return str_format(args)

    @reg("find")
    def s_find(args):
        return str_find_aux(args, True)

    @reg("match")
    def s_match(args):
        return str_find_aux(args, False)

    @reg("gmatch")
    def s_gmatch(args):
        return str_gmatch(args)

    @reg("gsub")
    def s_gsub(args):
        return str_gsub(args)

    @reg("dump")
    def s_dump(args):
        raise rt_error("unable to dump given function")

    return lib


def build_table_lib(interp):
    lib = LuaTable()

    def reg(name):
        def deco(fn):
            rawset(lib, name, LuaFunction(fn, name, True))
            return fn

        return deco

    @reg("insert")
    def t_insert(args):
        t = arg_table(args, 0, "insert")
        n = len(t.arr)
        if len(args) == 2:
            rawset(t, n + 1, args[1])
        elif len(args) == 3:
            pos = arg_int(args, 1, "insert")
            if pos < 1 or pos > n + 1:
                raise rt_error("bad argument #2 to 'insert' (position out of bounds)")
            for k in range(n, pos - 1, -1):
                rawset(t, k + 1, rawget(t, k))
            rawset(t, pos, args[2])
        else:
            raise rt_error("wrong number of arguments to 'insert'")
        return []

    @reg("remove")
    def t_remove(args):
        t = arg_table(args, 0, "remove")
        n = len(t.arr)
        pos = arg_int(args, 1, "remove", n) if len(args) > 1 and args[1] is not None else n
        if n == 0 and (len(args) < 2 or args[1] is None):
            return [None]
        if pos < 1 or pos > n + 1:
            if n == 0 and pos == 0:
                return [None]
            raise rt_error("bad argument #2 to 'remove' (position out of bounds)")
        v = rawget(t, pos)
        for k in range(pos, n):
            rawset(t, k, rawget(t, k + 1))
        if pos <= n:
            rawset(t, n, None)
        return [v]

    @reg("concat")
    def t_concat(args):
        t = arg_table(args, 0, "concat")
        sep = to_str(arg_at(args, 1)) or ""
        i = arg_int(args, 2, "concat", 1)
        j = arg_int(args, 3, "concat", len(t.arr)) if len(args) > 3 and args[3] is not None else len(t.arr)
        parts = []
        for k in range(i, j + 1):
            v = rawget(t, k)
            s = to_str(v)
            if s is None:
                raise rt_error("invalid value (at index %d) in table for 'concat'" % k)
            parts.append(s)
        return [sep.join(parts)]

    @reg("unpack")
    def t_unpack(args):
        return lua_unpack(args)

    @reg("getn")
    def t_getn(args):
        return [len(arg_table(args, 0, "getn").arr)]

    @reg("maxn")
    def t_maxn(args):
        t = arg_table(args, 0, "maxn")
        best = len(t.arr)
        for k in t.hash:
            if type(k) in (int, float) and k > best:
                best = k
        return [best]

    @reg("sort")
    def t_sort(args):
        t = arg_table(args, 0, "sort")
        comp = arg_at(args, 1)
        n = len(t.arr)
        items = [rawget(t, k) for k in range(1, n + 1)]
        if comp is None:

            def cmp(a, b):
                if op_lt(a, b):
                    return -1
                if op_lt(b, a):
                    return 1
                return 0

        else:

            def cmp(a, b):
                if truthy(first(call(comp, [a, b]))):
                    return -1
                if truthy(first(call(comp, [b, a]))):
                    return 1
                return 0

        items.sort(key=cmp_to_key(cmp))
        for k, v in enumerate(items, 1):
            rawset(t, k, v)
        return []

    return lib


def lua_unpack(args):
    t = arg_table(args, 0, "unpack")
    i = arg_int(args, 1, "unpack", 1)
    j = arg_int(args, 2, "unpack", len(t.arr)) if len(args) > 2 and args[2] is not None else len(t.arr)
    if j - i >= 1000000:
        raise rt_error("too many results to unpack")
    return [rawget(t, k) for k in range(i, j + 1)]


def build_math_lib(interp):
    lib = LuaTable()
    rng = random.Random(1234)

    def reg(name, fn):
        rawset(lib, name, LuaFunction(fn, name, True))

    def wrap1(name, pyfn):
        def f(args):
            x = arg_num(args, 0, name)
            try:
                return [norm(pyfn(x))]
            except (ValueError, OverflowError):
                return [NAN]

        reg(name, f)

    def floor(args):
        x = arg_num(args, 0, "floor")
        if x != x or abs(x) == INF:
            return [x]
        return [norm(float(math.floor(x)))] if abs(x) >= 2 ** 53 else [math.floor(x)]

    def ceil(args):
        x = arg_num(args, 0, "ceil")
        if x != x or abs(x) == INF:
            return [x]
        return [norm(float(math.ceil(x)))] if abs(x) >= 2 ** 53 else [math.ceil(x)]

    reg("floor", floor)
    reg("ceil", ceil)
    wrap1("abs", abs)
    wrap1("sqrt", math.sqrt)
    wrap1("sin", math.sin)
    wrap1("cos", math.cos)
    wrap1("tan", math.tan)
    wrap1("asin", math.asin)
    wrap1("acos", math.acos)
    wrap1("atan", math.atan)
    wrap1("exp", math.exp)
    wrap1("log10", math.log10)
    wrap1("deg", math.degrees)
    wrap1("rad", math.radians)

    def log(args):
        x = arg_num(args, 0, "log")
        try:
            if len(args) > 1 and args[1] is not None:
                return [norm(math.log(x) / math.log(arg_num(args, 1, "log")))]
            return [norm(math.log(x))]
        except (ValueError, ZeroDivisionError):
            return [-INF if x == 0 else NAN]

    reg("log", log)

    def atan2(args):
        return [norm(math.atan2(arg_num(args, 0, "atan2"), arg_num(args, 1, "atan2")))]

    reg("atan2", atan2)

    def pow_(args):
        try:
            return [norm(math.pow(arg_num(args, 0, "pow"), arg_num(args, 1, "pow")))]
        except OverflowError:
            return [INF]
        except ValueError:
            return [NAN]

    reg("pow", pow_)

    def fmod(args):
        a = arg_num(args, 0, "fmod")
        b = arg_num(args, 1, "fmod")
        if b == 0:
            return [NAN]
        return [norm(math.fmod(a, b))]

    reg("fmod", fmod)

    def modf(args):
        x = arg_num(args, 0, "modf")
        if abs(x) == INF:
            return [x, 0]
        ip = float(math.trunc(x))
        return [norm(ip), norm(x - ip)]

    reg("modf", modf)

    def mmax(args):
        best = arg_num(args, 0, "max")
        for i in range(1, len(args)):
            v = arg_num(args, i, "max")
            if v > best:
                best = v
        return [best]

    def mmin(args):
        best = arg_num(args, 0, "min")
        for i in range(1, len(args)):
            v = arg_num(args, i, "min")
            if v < best:
                best = v
        return [best]

    reg("max", mmax)
    reg("min", mmin)

    def mrandom(args):
        if not args:
            return [rng.random()]
        lo = 1
        hi = arg_int(args, 0, "random")
        if len(args) > 1:
            lo = hi
            hi = arg_int(args, 1, "random")
        if lo > hi:
            raise rt_error("bad argument #%d to 'random' (interval is empty)" % len(args))
        return [rng.randint(lo, hi)]

    reg("random", mrandom)

    def mseed(args):
        rng.seed(arg_num(args, 0, "randomseed", 0))
        return []

    reg("randomseed", mseed)
    rawset(lib, "huge", INF)
    rawset(lib, "pi", math.pi)
    return lib


def build_os_lib(interp):
    lib = LuaTable()

    def reg(name):
        def deco(fn):
            rawset(lib, name, LuaFunction(fn, name, True))
            return fn

        return deco

    @reg("time")
    def o_time(args):
        return [int(time.time())]

    @reg("clock")
    def o_clock(args):
        return [time.process_time()]

    @reg("date")
    def o_date(args):
        fmt = to_str(arg_at(args, 0)) or "%c"
        if fmt.startswith("!"):
            fmt = fmt[1:]
        return [time.strftime(fmt)]

    @reg("getenv")
    def o_getenv(args):
        return [None]

    @reg("difftime")
    def o_difftime(args):
        return [arg_num(args, 0, "difftime") - arg_num(args, 1, "difftime", 0)]

    @reg("exit")
    def o_exit(args):
        raise LuaError("os.exit called")

    return lib


def build_debug_lib(interp):
    lib = LuaTable()

    def reg(name):
        def deco(fn):
            rawset(lib, name, LuaFunction(fn, name, True))
            return fn

        return deco

    @reg("getinfo")
    def d_getinfo(args):
        t = LuaTable()
        target = arg_at(args, 0)
        native = type(target) is LuaFunction and target.native
        rawset(t, "what", "C" if native else "Lua")
        rawset(t, "source", "=[C]" if native else "@" + CHUNK)
        rawset(t, "short_src", "[C]" if native else CHUNK)
        rawset(t, "currentline", -1 if native else 1)
        rawset(t, "linedefined", -1 if native else 1)
        rawset(t, "nups", 0)
        if type(target) is LuaFunction:
            rawset(t, "func", target)
        return [t]

    @reg("traceback")
    def d_traceback(args):
        msg = arg_at(args, 0)
        if msg is not None and type(msg) is not str and type(msg) not in (int, float):
            return [msg]
        return [(to_str(msg) + "\n" if msg is not None else "") + "stack traceback:"]

    hook_state = {"fn": None}

    @reg("gethook")
    def d_gethook(args):
        return [hook_state["fn"]]

    @reg("sethook")
    def d_sethook(args):
        fn = arg_at(args, 0)
        if type(fn) is not LuaFunction:
            hook_state["fn"] = None
            return []
        count = arg_at(args, 2)
        hook_state["fn"] = fn
        if type(count) in (int, float) and count > 0:
            for _ in range(64):
                fn.fn([])
        return []

    @reg("getlocal")
    def d_getlocal(args):
        return [None]

    @reg("getupvalue")
    def d_getupvalue(args):
        return [None]

    @reg("getmetatable")
    def d_getmetatable(args):
        return [get_meta(arg_at(args, 0))]

    @reg("getregistry")
    def d_getregistry(args):
        return [LuaTable()]

    return lib


class Interpreter:
    def __init__(self, hooks=None, debug=False):
        self.G = LuaTable()
        self.events = []
        self.hooks = hooks or {}
        self.compiler = Compiler(self.G, debug)
        self.loaded = []
        self.install()

    def log(self, kind, *data):
        self.events.append((kind,) + data)

    def compile(self, src):
        stmts = parse_chunk(src)
        return self.compiler.make_main(stmts)

    def run(self, src, args=()):
        fn = self.compile(src)
        return fn.fn(list(args))

    def install(self):
        G = self.G
        interp = self

        def reg(name):
            def deco(fn):
                rawset(G, name, LuaFunction(fn, name, True))
                return fn

            return deco

        string_lib = build_string_lib(self)
        rawset(STRING_META, "__index", string_lib)
        rawset(G, "string", string_lib)
        rawset(G, "table", build_table_lib(self))
        rawset(G, "math", build_math_lib(self))
        rawset(G, "os", build_os_lib(self))
        rawset(G, "debug", build_debug_lib(self))
        rawset(G, "_G", G)
        rawset(G, "_VERSION", "Lua 5.1")

        @reg("print")
        def g_print(args):
            interp.log("print", list(args))
            return []

        @reg("type")
        def g_type(args):
            if not args:
                raise bad_arg(0, "type", "value", "no value")
            return [lua_type(args[0])]

        @reg("tostring")
        def g_tostring(args):
            if not args:
                raise bad_arg(0, "tostring", "value", "no value")
            return [lua_tostring(args[0])]

        @reg("tonumber")
        def g_tonumber(args):
            v = arg_at(args, 0)
            base = arg_at(args, 1)
            if base is None or base == 10:
                if type(v) in (int, float):
                    return [v]
                if type(v) is str:
                    return [str_to_number(v)]
                return [None]
            s = to_str(v)
            if s is None:
                return [None]
            try:
                return [int(s.strip().lower(), int(base))]
            except ValueError:
                return [None]

        def lua_next(args):
            t = arg_table(args, 0, "next")
            from .luavm import table_next

            k, v = table_next(t, arg_at(args, 1))
            if k is None:
                return [None]
            return [k, v]

        next_fn = LuaFunction(lua_next, "next", True)
        rawset(G, "next", next_fn)

        @reg("pairs")
        def g_pairs(args):
            t = arg_at(args, 0)
            mm = get_mm(t, "__pairs")
            if mm is not None:
                return call(mm, [t])[:3]
            if type(t) is not LuaTable:
                raise bad_arg(0, "pairs", "table", lua_type(t))
            return [next_fn, t, None]

        def ipairs_iter(args):
            t = args[0]
            i = args[1] + 1
            v = rawget(t, i) if type(t) is LuaTable else index(t, i)
            if v is None:
                return [None]
            return [i, v]

        ipairs_fn = LuaFunction(ipairs_iter, "ipairs_iter", True)

        @reg("ipairs")
        def g_ipairs(args):
            t = arg_at(args, 0)
            if type(t) is not LuaTable:
                raise bad_arg(0, "ipairs", "table", lua_type(t))
            return [ipairs_fn, t, 0]

        @reg("rawget")
        def g_rawget(args):
            return [rawget(arg_table(args, 0, "rawget"), arg_at(args, 1))]

        @reg("rawset")
        def g_rawset(args):
            t = arg_table(args, 0, "rawset")
            rawset(t, arg_at(args, 1), arg_at(args, 2))
            return [t]

        @reg("rawequal")
        def g_rawequal(args):
            return [raw_eq(arg_at(args, 0), arg_at(args, 1))]

        @reg("rawlen")
        def g_rawlen(args):
            v = arg_at(args, 0)
            if type(v) is LuaTable:
                return [len(v.arr)]
            if type(v) is str:
                return [len(v)]
            raise rt_error("table or string expected")

        @reg("setmetatable")
        def g_setmetatable(args):
            t = arg_table(args, 0, "setmetatable")
            mt = arg_at(args, 1)
            if mt is not None and type(mt) is not LuaTable:
                raise bad_arg(1, "setmetatable", "nil or table", lua_type(mt))
            if t.meta is not None and t.meta.hash.get("__metatable") is not None:
                raise rt_error("cannot change a protected metatable")
            t.meta = mt
            return [t]

        @reg("getmetatable")
        def g_getmetatable(args):
            mt = get_meta(arg_at(args, 0))
            if mt is None:
                return [None]
            prot = mt.hash.get("__metatable")
            if prot is not None:
                return [prot]
            return [mt]

        @reg("select")
        def g_select(args):
            n = arg_at(args, 0)
            if n == "#":
                return [len(args) - 1]
            n = arg_int(args, 0, "select")
            rest = args[1:]
            if n < 0:
                n = len(rest) + n
                if n < 0:
                    raise bad_arg(0, "select", "index out of range", "number")
                return rest[n:]
            if n == 0:
                raise bad_arg(0, "select", "index out of range", "number")
            return rest[n - 1:]

        @reg("unpack")
        def g_unpack(args):
            return lua_unpack(args)

        @reg("assert")
        def g_assert(args):
            if not args:
                raise bad_arg(0, "assert", "value", "no value")
            if not truthy(args[0]):
                msg = args[1] if len(args) > 1 else "assertion failed!"
                raise LuaError(msg)
            return list(args)

        @reg("error")
        def g_error(args):
            msg = arg_at(args, 0)
            level = arg_at(args, 1)
            level = 1 if level is None else level
            if type(msg) is str and level != 0:
                msg = "%s:1: %s" % (CHUNK, msg)
            raise LuaError(msg)

        @reg("pcall")
        def g_pcall(args):
            if not args:
                raise bad_arg(0, "pcall", "value", "no value")
            try:
                return [True] + list(call(args[0], args[1:]))
            except LuaError as e:
                return [False, e.value]
            except RecursionError:
                return [False, "stack overflow"]

        @reg("xpcall")
        def g_xpcall(args):
            f = arg_at(args, 0)
            handler = arg_at(args, 1)
            try:
                return [True] + list(call(f, args[2:]))
            except LuaError as e:
                return [False] + list(call(handler, [e.value]))
            except RecursionError:
                return [False] + list(call(handler, ["stack overflow"]))

        @reg("collectgarbage")
        def g_collectgarbage(args):
            opt = arg_at(args, 0) or "collect"
            if opt == "count":
                return [256.5]
            return [0]

        @reg("newproxy")
        def g_newproxy(args):
            u = LuaUserdata()
            a = arg_at(args, 0)
            if a is True:
                u.meta = LuaTable()
            elif type(a) is LuaUserdata:
                u.meta = a.meta
            return [u]

        @reg("getfenv")
        def g_getfenv(args):
            return [G]

        @reg("setfenv")
        def g_setfenv(args):
            return [arg_at(args, 0)]

        def do_load(src, chunkname):
            interp.log("load", src)
            interp.loaded.append(src)
            try:
                return [interp.compile(src)]
            except (SyntaxError, IndexError, KeyError, AttributeError) as e:
                return [None, "%s: %s" % (CHUNK, e)]

        @reg("loadstring")
        def g_loadstring(args):
            src = arg_str(args, 0, "loadstring")
            return do_load(src, arg_at(args, 1))

        @reg("load")
        def g_load(args):
            chunk = arg_at(args, 0)
            if type(chunk) is LuaFunction:
                parts = []
                while True:
                    piece = first(call(chunk, []))
                    if piece is None or piece == "":
                        break
                    parts.append(piece)
                return do_load("".join(parts), arg_at(args, 1))
            return do_load(arg_str(args, 0, "load"), arg_at(args, 1))

        return G
