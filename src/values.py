import re
from .folding import num_norm
from .parser import INF


class Sym:
    __slots__ = ("name", "packed", "known")

    def __init__(self, name):
        self.name = name
        self.packed = False
        self.known = False


class Obj:
    __slots__ = ("d", "mt", "oid", "escaped", "name", "lib")

    def __init__(self, oid=0, lib=None):
        self.d = {}
        self.mt = None
        self.oid = oid
        self.escaped = False
        self.name = None
        self.lib = lib


class Clo:
    __slots__ = ("key", "caps", "name", "arity")

    def __init__(self, key, caps, arity=None):
        self.key = key
        self.caps = caps
        self.name = None
        self.arity = arity


class Bi:
    __slots__ = ("name", "fn", "kind")

    def __init__(self, name, fn, kind="pure"):
        self.name = name
        self.fn = fn
        self.kind = kind


class Stuck(Exception):
    pass


class LuaErr(Exception):
    def __init__(self, value):
        Exception.__init__(self, value)
        self.value = value


class Frame:
    def __init__(self, caps, args):
        self.up = caps
        self.args = args
        self.regs = {}
        self.tmps = {}
        self.multi = {}


def nk(k):
    if type(k) is bool:
        return ("b", k)
    if type(k) is float:
        return num_norm(k)
    return k


def isnum(v):
    return type(v) in (int, float) and type(v) is not bool


def is_sym(v):
    return type(v) is Sym


def lt(v):
    if v is None:
        return "nil"
    t = type(v)
    if t is bool:
        return "boolean"
    if t in (int, float):
        return "number"
    if t is str:
        return "string"
    if t is Obj:
        return "table"
    if t in (Clo, Bi):
        return "function"
    return "userdata"


def tostr(v, ev=None):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if type(v) is int:
        return str(v)
    if type(v) is float:
        if v != v:
            return "nan"
        if v in (INF, -INF):
            return "inf" if v > 0 else "-inf"
        return "%.14g" % v
    if type(v) is str:
        return v
    if type(v) is Obj:
        return "table: 0x%08x" % (0x10000000 + v.oid * 8)
    if type(v) in (Clo, Bi):
        return "function: 0x%08x" % (0x20000000 + (id(v) & 0xFFFFFF))
    return "userdata"


def tonum(v, base=None):
    if isnum(v):
        return v
    if type(v) is str:
        s = v.strip()
        try:
            if base:
                return int(s, int(base))
            if s.lower().startswith("0x"):
                return int(s, 16)
            f = float(s)
            if f != f or f in (INF, -INF):
                return None
            return num_norm(f)
        except ValueError:
            return None
    return None


def lua_pat(pat):
    out = []
    i = 0
    n = len(pat)
    classes = {"d": "[0-9]", "s": "[ \\t\\n\\r\\f\\v]", "w": "[A-Za-z0-9]", "a": "[A-Za-z]", "l": "[a-z]", "u": "[A-Z]", "x": "[0-9A-Fa-f]", "p": "[!-/:-@\\[-`{-~]", "c": "[\\x00-\\x1f]"}
    while i < n:
        c = pat[i]
        if c == "%":
            i += 1
            e = pat[i]
            if e in classes:
                out.append(classes[e])
            elif e.lower() in classes and e.isupper():
                out.append("[^" + classes[e.lower()][1:])
            else:
                out.append(re.escape(e))
        elif c == "[":
            j = i + 1
            buf = ["["]
            if j < n and pat[j] == "^":
                buf.append("^")
                j += 1
            first_item = True
            while j < n and (pat[j] != "]" or first_item):
                first_item = False
                if pat[j] == "%":
                    j += 1
                    e = pat[j]
                    if e in classes:
                        buf.append(classes[e][1:-1])
                    else:
                        buf.append(re.escape(e))
                else:
                    buf.append(re.escape(pat[j]) if pat[j] in "\\[]^" else pat[j])
                j += 1
            buf.append("]")
            out.append("".join(buf))
            i = j
        elif c == "-":
            out.append("*?")
        elif c in "*+?":
            out.append(c)
        elif c in "()":
            out.append(c)
        elif c == "^" and i == 0:
            out.append("^")
        elif c == "$" and i == n - 1:
            out.append("$")
        elif c == ".":
            out.append("[\\s\\S]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out))


ARITH = ("+", "-", "*", "/", "%", "^")


COMPARE = ("<", ">", "<=", ">=")
