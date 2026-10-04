import re
from .emitter import num, q
from .lexer import KEYWORDS
from .values import Bi, Clo, Obj, Sym


IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


INLINE = {}


def rv(v):
    if type(v) is Sym and v.name in INLINE:
        return INLINE[v.name]
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if type(v) in (int, float):
        return num(v)
    if type(v) is str:
        return q(v)
    if type(v) is Sym:
        return v.name + ("[1]" if v.packed else "")
    if type(v) is Obj:
        return v.lib if v.lib else v.name
    if type(v) is Clo:
        return v.name
    if type(v) is Bi:
        return v.name
    return "nil"


def rexpr(e):
    k = e[0]
    if k == "bin":
        return "(%s %s %s)" % (rv(e[2]), e[1], rv(e[3]))
    if k == "un":
        return "(%s%s)" % ("not " if e[1] == "not" else e[1], rv(e[2]))
    return "(%s %s %s)" % (rv(e[1]), k, rv(e[2]))


def member(base, key):
    if type(key) is str and IDENT.match(key) and key not in KEYWORDS:
        return "%s.%s" % (rv(base), key)
    return "%s[%s]" % (rv(base), rv(key))


EFFECTS = ("call", "setindex", "setglobal", "obj", "clo")


def count_uses(x, counts):
    if isinstance(x, (tuple, list)):
        for y in x:
            count_uses(y, counts)
    elif type(x) is Sym:
        counts[x.name] = counts.get(x.name, 0) + 1


def entry_uses(r):
    counts = {}
    if r[0] in ("global", "index", "expr", "call"):
        count_uses(list(r[2:]), counts)
    else:
        count_uses(list(r[1:]), counts)
    return counts


def inline_expr(r):
    if r[0] == "global":
        key = r[2]
        if type(key) is str and IDENT.match(key) and key not in KEYWORDS:
            return key
        return "_ENV[%s]" % rv(key)
    return member(r[2], r[3])


def render_residual(res, extra=()):
    totals = {}
    for r in res:
        for n, c in entry_uses(r).items():
            totals[n] = totals.get(n, 0) + c
    for v in extra:
        count_uses(v, totals)
    out = []
    pending = []
    defined = set()
    deferred = {}
    INLINE.clear()

    def ready(v):
        if type(v) is Obj and not v.lib:
            return v.name in defined
        if type(v) is Clo:
            return v.name in defined
        return True

    def defer_on(v, line):
        deferred.setdefault(v.name, []).append(line)

    def settle(name):
        defined.add(name)
        for line in deferred.pop(name, []):
            out.append(line)

    def drain(used, keep_used):
        keep = []
        for name, expr in pending:
            if name in used:
                if keep_used:
                    keep.append((name, expr))
            else:
                INLINE.pop(name, None)
                out.append("local %s = %s" % (name, expr))
        pending[:] = keep

    for r in res:
        k = r[0]
        used = entry_uses(r)
        if k in ("global", "index") and totals.get(r[1].name, 0) == 1 and not r[1].packed:
            expr = inline_expr(r)
            INLINE[r[1].name] = expr
            pending[:] = [(n, e) for n, e in pending if n not in used]
            pending.append((r[1].name, expr))
            continue
        if k in EFFECTS:
            drain(used, False)
        else:
            pending[:] = [(n, e) for n, e in pending if n not in used]
        if k == "call":
            call = "%s(%s)" % (rv(r[2]), ", ".join(rv(a) for a in r[3]))
            out.append(call if r[1] is None or totals.get(r[1].name, 0) == 0 else "local %s = %s" % (r[1].name, call))
        elif k == "expr":
            out.append("local %s = %s" % (r[1].name, rexpr(r[2])))
        elif k == "global":
            key = r[2]
            out.append("local %s = %s" % (r[1].name, key if type(key) is str and IDENT.match(key) and key not in KEYWORDS else "_ENV[%s]" % rv(key)))
        elif k == "index":
            out.append("local %s = %s" % (r[1].name, member(r[2], r[3])))
        elif k == "setindex":
            out.append("%s = %s" % (member(r[1], r[2]), rv(r[3])))
        elif k == "setglobal":
            key = r[1]
            out.append("%s = %s" % (key if type(key) is str and IDENT.match(key) and key not in KEYWORDS else "_ENV[%s]" % rv(key), rv(r[2])))
        elif k == "obj":
            obj = r[1]
            now = []
            for a, b in r[2]:
                if ready(b) and (b is not obj):
                    now.append((a, b))
                else:
                    defer_on(b, "%s[%s] = %s" % (obj.name, rv(a), rv(b)))
            items = ", ".join("[%s] = %s" % (rv(a), rv(b)) for a, b in now)
            out.append("local %s = {%s}" % (obj.name, items))
            settle(obj.name)
            if r[3] is not None:
                if ready(r[3]):
                    out.append("setmetatable(%s, %s)" % (obj.name, rv(r[3])))
                else:
                    defer_on(r[3], "setmetatable(%s, %s)" % (obj.name, rv(r[3])))
        elif k == "clo":
            c = r[1]
            out.append("local %s = make[%d]({%s})" % (c.name, c.key, ", ".join(rv(x) for x in c.caps)))
            settle(c.name)
    for name, expr in pending:
        INLINE.pop(name, None)
        out.append("local %s = %s" % (name, expr))
    INLINE.clear()
    return "\n".join(out) + ("\n" if out else "")
