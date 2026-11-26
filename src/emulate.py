import re
import sys
import threading

from .luastd import Interpreter
from .luavm import LuaError, LuaFunction, LuaTable, StepLimit, fmt_num, rawget, rawset, set_budget

DEFAULT_SYMBOLIC = (
    "wait spawn delay tick time warn game workspace script shared task typeof "
    "Instance Vector3 Vector2 CFrame Color3 UDim UDim2 Enum coroutine utf8 bit32"
).split()

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
KEYWORDS = frozenset(
    "and break do else elseif end false for function if in local nil not or repeat return then true until while".split()
)


def quote(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif 32 <= o < 127:
            out.append(ch)
        else:
            out.append("\\%03d" % o)
    out.append('"')
    return "".join(out)


def render_value(v, depth=0):
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if type(v) is str:
        return quote(v)
    if type(v) in (int, float):
        return fmt_num(v)
    if isinstance(v, Sym):
        return v.expr
    if type(v) is LuaTable:
        if depth >= 3:
            return "{}"
        items = []
        n = len(v.arr)
        for i in range(n):
            items.append(render_value(v.arr[i], depth + 1))
        for k in v.hash:
            val = v.hash[k]
            if type(k) is str and IDENT.match(k) and k not in KEYWORDS:
                items.append("%s = %s" % (k, render_value(val, depth + 1)))
            elif type(k) is str:
                items.append("[%s] = %s" % (quote(k), render_value(val, depth + 1)))
            elif type(k) in (int, float):
                items.append("[%s] = %s" % (fmt_num(k), render_value(val, depth + 1)))
            else:
                items.append("[nil] = %s" % render_value(val, depth + 1))
        return "{%s}" % ", ".join(items)
    if type(v) is LuaFunction:
        return "function() end"
    return "nil"


class Sym:
    def __init__(self, interp, expr, parent=None, key=None):
        self.interp = interp
        self.expr = expr
        self.parent = parent
        self.key = key

    def sym_index(self, key):
        if type(key) is str and IDENT.match(key) and key not in KEYWORDS:
            expr = "%s.%s" % (self.expr, key)
        else:
            expr = "%s[%s]" % (self.expr, render_value(key))
        return Sym(self.interp, expr, self, key)

    def sym_setindex(self, key, val):
        target = self.sym_index(key).expr
        self.interp.log("stmt", "%s = %s" % (target, render_value(val)))

    def sym_call(self, args):
        interp = self.interp
        rest = list(args)
        if self.parent is not None and rest and rest[0] is self.parent and type(self.key) is str and IDENT.match(self.key):
            text = "%s:%s(%s)" % (self.parent.expr, self.key, ", ".join(render_value(a) for a in rest[1:]))
        else:
            text = "%s(%s)" % (self.expr, ", ".join(render_value(a) for a in rest))
        interp.sym_counter += 1
        name = "v%d" % interp.sym_counter
        interp.log("call", text, name)
        return [Sym(interp, name)]

    def sym_tostring(self):
        return "userdata: %s" % self.expr


def install_symbolic(interp, names):
    interp.sym_counter = 0
    for name in names:
        if rawget(interp.G, name) is None:
            rawset(interp.G, name, Sym(interp, name))


def render_events(events):
    stmts = []
    for ev in events:
        kind = ev[0]
        if kind == "print":
            stmts.append(("print(%s)" % ", ".join(render_value(a) for a in ev[1]), None))
        elif kind == "load":
            stmts.append(("loadstring(%s)" % quote(ev[1]), None))
        elif kind == "call":
            stmts.append((ev[1], ev[2]))
        elif kind == "stmt":
            stmts.append((ev[1], None))
    lines = []
    for i, (text, name) in enumerate(stmts):
        used = False
        if name is not None:
            pat = re.compile(r"(?<![\w.])%s(?![\w])" % re.escape(name))
            used = any(pat.search(t) for t, _ in stmts[i + 1:])
        lines.append("local %s = %s" % (name, text) if used else text)
    return lines


class EmulationResult:
    def __init__(self):
        self.lines = []
        self.error = None
        self.limit_hit = False
        self.loaded = []
        self.returned = []

    @property
    def text(self):
        return "\n".join(self.lines) + ("\n" if self.lines else "")


def run_in_big_stack(work):
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(200000)
    old_stack = threading.stack_size()
    started = False
    try:
        for megabytes in (512, 128, 32):
            try:
                threading.stack_size(megabytes * 1024 * 1024)
                th = threading.Thread(target=work)
                th.start()
            except (RuntimeError, ValueError):
                continue
            started = True
            th.join()
            break
    finally:
        threading.stack_size(old_stack)
        sys.setrecursionlimit(old_limit)
    if not started:
        raise RuntimeError("could not start the emulation thread")


def emulate(src, assume=(), strict=False, step_limit=50000000, args=()):
    result = EmulationResult()
    holder = {}

    def work():
        interp = Interpreter()
        names = set(assume) if strict else set(DEFAULT_SYMBOLIC) | set(assume)
        install_symbolic(interp, names)
        set_budget(step_limit)
        holder["interp"] = interp
        try:
            holder["ret"] = interp.run(src, args)
        except LuaError as e:
            holder["error"] = e.value
        except StepLimit:
            holder["limit"] = True
        except RecursionError:
            holder["error"] = "stack overflow"
        except (SyntaxError, IndexError, KeyError) as e:
            holder["syntax"] = str(e)

    run_in_big_stack(work)
    interp = holder.get("interp")
    if "syntax" in holder:
        raise SyntaxError(holder["syntax"])
    if interp is not None:
        result.lines = render_events(interp.events)
        result.loaded = list(interp.loaded)
    result.error = holder.get("error")
    result.limit_hit = bool(holder.get("limit"))
    result.returned = holder.get("ret") or []
    return result
