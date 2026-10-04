import re
from .lexer import unescape


def q(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif 32 <= o < 127:
            out.append(ch)
        else:
            out.append("\\%03d" % o)
    out.append('"')
    return "".join(out)


def num(v):
    if type(v) is float:
        if v == int(v) and abs(v) < 2 ** 53:
            return str(int(v))
        return repr(v)
    return str(v)


class Emitter:
    def __init__(self, an):
        self.an = an
        self.funcs = {}
        self.order = []
        self.multi = set()
        self.discover(an.entry[0])

    def succs(self, r):
        nx = r["next"]
        out = []

        def tg(x):
            if x[0] == "goto":
                out.append(x[1])
            elif x[0] == "branch":
                tg(x[2])
                tg(x[3])

        tg(nx)
        return out

    def discover(self, entry):
        if entry in self.funcs:
            return
        nodes = {}
        order = []
        stack = [entry]
        clos = []
        while stack:
            k = stack.pop()
            if k in nodes:
                continue
            r = self.an.lift(k)
            nodes[k] = r
            order.append(k)
            for s in reversed(self.succs(r)):
                stack.append(s)
            for s in r["stmts"]:
                if s[0] == "let" and s[2][0] == "closure":
                    clos.append((s[2][1], s[2][3]))
        self.funcs[entry] = (nodes, order, self.an.lift(entry)["rec"])
        self.order.append(entry)
        self.arity = getattr(self, "arity", {})
        for k, n in clos:
            self.arity[k] = n
            self.discover(k)

    def scan_multi(self, e):
        if isinstance(e, tuple):
            if e and e[0] == "multi" and isinstance(e[1], tuple) and e[1] and e[1][0] == "tmp":
                self.multi.add(e[1][1])
            for x in e:
                self.scan_multi(x)
        elif isinstance(e, list):
            for x in e:
                self.scan_multi(x)

    def tn(self, tid):
        return "t%d_%d" % (tid[0] % 100000, tid[1])

    def x(self, e):
        k = e[0]
        if k == "const":
            v = e[1]
            if v is None:
                return "nil"
            if v is True:
                return "true"
            if v is False:
                return "false"
            if type(v) is str:
                return q(v)
            return num(v)
        if k == "reg":
            return "r_" + e[1]
        if k == "tmp":
            if e[1] in self.multi:
                return self.tn(e[1]) + "[1]"
            return self.tn(e[1])
        if k == "bin":
            return "(%s %s %s)" % (self.x(e[2]), e[1], self.x(e[3]))
        if k == "un":
            return "(%s%s)" % (e[1] if e[1] != "not" else "not ", self.x(e[2]))
        if k == "and":
            return "(%s and %s)" % (self.x(e[1]), self.x(e[2]))
        if k == "or":
            return "(%s or %s)" % (self.x(e[1]), self.x(e[2]))
        if k == "builtin":
            return e[1]
        if k == "env":
            return "_ENV"
        if k == "varargs":
            return "{...}"
        if k == "helper":
            return "helper"
        if k == "cap":
            return "up[%d]" % e[1]
        if k == "arg":
            return "(select(%d, ...))" % e[1]
        if k == "args":
            return "{...}"
        if k == "vararg":
            return "..."
        if k == "table":
            return "{" + ", ".join(self.titem(i) for i in e[1]) + "}"
        if k == "first":
            return "(%s)" % self.x(e[1])
        if k == "unpack":
            return "table.unpack(%s)" % self.x(e[1])
        if k == "select":
            return "select(%d, %s)" % (e[1], self.xm(e[2]))
        if k == "multi":
            return self.xm(e[1])
        if k == "cellref":
            return self.x(e[1])
        return "nil"

    def xm(self, e):
        if e[0] == "tmp" and e[1] in self.multi:
            return "table.unpack(%s, 1, %s.n)" % (self.tn(e[1]), self.tn(e[1]))
        return self.x(e)

    def titem(self, i):
        if i[0] == "pos":
            return self.x(i[1])
        return "[%s] = %s" % (self.x(i[1]), self.x(i[2]))

    def args(self, lst):
        return ", ".join(self.x(a) for a in lst)

    def let_rhs(self, e):
        k = e[0]
        if k == "call":
            return "%s(%s)" % (self.x(e[1]), self.args(e[2]))
        if k == "index":
            return "%s[%s]" % (self.x(e[1]), self.x(e[2]))
        if k == "global":
            return "_ENV[%s]" % self.x(e[1])
        if k == "cellval":
            return "%s.v" % self.x(e[1])
        if k == "alloc":
            return "{}"
        if k == "table":
            return "{" + ", ".join(self.titem(i) for i in e[1]) + "}"
        if k == "closure":
            return "make[%d]({%s})" % (e[1], ", ".join(self.x(c) for c in e[2]))
        return "nil"

    def block_lines(self, r, ind):
        out = []
        pad = "    " * ind
        for s in r["stmts"]:
            if s[0] == "let":
                rhs = self.let_rhs(s[2])
                if s[1] in self.multi and s[2][0] == "call":
                    out.append("%slocal %s = table.pack(%s)" % (pad, self.tn(s[1]), rhs))
                else:
                    out.append("%slocal %s = %s" % (pad, self.tn(s[1]), rhs))
            elif s[0] == "cellset":
                out.append("%s%s.v = %s" % (pad, self.x(s[1]), self.x(s[2])))
            elif s[0] == "setglobal":
                out.append("%s_ENV[%s] = %s" % (pad, self.x(s[1]), self.x(s[2])))
            elif s[0] == "setindex":
                out.append("%s%s[%s] = %s" % (pad, self.x(s[1]), self.x(s[2]), self.x(s[3])))
        nx = r["next"]
        if nx[0] == "ret":
            ret = r["ret"]
            if ret is not None and ret[0] == "table":
                vals = ", ".join(self.x(i[1]) for i in ret[1] if i[0] == "pos")
                out.append("%sreturn %s" % (pad, vals) if vals else "%sreturn" % pad)
            else:
                out.append("%sreturn" % pad)
            return out
        cond = None
        if nx[0] == "branch":
            cond = "c_%d" % (r["key"] % 100000)
            out.append("%slocal %s = %s" % (pad, cond, self.x(nx[1])))
        w = sorted(r["writes"].items())
        w = [(n, v) for n, v in w if n not in (self.an.disp["state"], self.an.disp["ret"])]
        if w:
            out.append("%s%s = %s" % (pad, ", ".join("r_" + n for n, _ in w), ", ".join(self.x(v) for _, v in w)))
        if nx[0] == "goto":
            out.append("%spc = %d" % (pad, nx[1]))
        else:
            a, b = nx[2], nx[3]

            def tgt(t):
                return "pc = %d" % t[1] if t[0] == "goto" else "pc = nil"

            out.append("%sif %s then %s else %s end" % (pad, cond, tgt(a), tgt(b)))
        return out

    def regs_of(self, nodes):
        regs = set()

        def walk(e):
            if isinstance(e, tuple):
                if len(e) == 2 and e[0] == "reg" and isinstance(e[1], str):
                    regs.add(e[1])
                for x in e:
                    walk(x)
            elif isinstance(e, list):
                for x in e:
                    walk(x)

        for r in nodes.values():
            walk(r["stmts"])
            walk(r["next"])
            walk(r["ret"])
            for n, v in r["writes"].items():
                if n not in (self.an.disp["state"], self.an.disp["ret"]):
                    regs.add(n)
                walk(v)
        return sorted(regs)

    def render(self, tail=None, resume=None):
        for nodes, order, rec in self.funcs.values():
            for r in nodes.values():
                self.scan_multi(r["stmts"])
                self.scan_multi(r["ret"])
                self.scan_multi(r["next"])
                self.scan_multi(list(r["writes"].values()))
        out = ["local make = {}"]
        if resume is not None:
            out.append("local RESUME")
        out.append("")
        for entry in self.order:
            nodes, order, rec = self.funcs[entry]
            out.append("make[%d] = function(up)" % entry)
            out.append("    return function(...)")
            regs = self.regs_of(nodes)
            if regs:
                out.append("        local " + ", ".join("r_" + r for r in regs))
            for reg, expr in self.an.prologue:
                if reg in regs:
                    out.append("        r_%s = %s" % (reg, self.x(self.conv_prologue(expr))))
            out.append("        local pc = %d" % entry)
            if resume is not None and entry == self.an.entry[0]:
                out.append("        if RESUME then")
                for reg in regs:
                    if reg in resume:
                        out.append("            r_%s = RESUME.regs[%s]" % (reg, q(reg)))
                out.append("            pc = RESUME.pc")
                out.append("        end")
            out.append("        while pc do")
            first = True
            for k in order:
                out.append("            %s pc == %d then" % ("if" if first else "elseif", k))
                first = False
                out.extend(self.block_lines(nodes[k], 4))
            out.append("            else")
            out.append("                error(\"invalid state\")")
            out.append("            end")
            out.append("        end")
            out.append("    end")
            out.append("end")
            out.append("")
        if tail is None:
            out.append("return make[%d]({})(...)" % self.an.entry[0])
        else:
            out.extend(tail)
        return "\n".join(out) + "\n"

    def conv_prologue(self, e):
        if e[0] == "or":
            return ("or", self.conv_prologue(e[1]), self.conv_prologue(e[2]))
        if e[0] == "and":
            return ("and", self.conv_prologue(e[1]), self.conv_prologue(e[2]))
        if e[0] == "index":
            return ("builtin", "%s.%s" % (self.x(e[1]), e[2][1]))
        return e


def collect_strings(an):
    pat = re.compile(r"(?<![\w.])%s\(\"((?:[^\"\\]|\\.)*)\",(\d+)((?:,\d+)+)\)" % re.escape(an.names["decoder"]))
    out = []
    for m in pat.finditer(an.src):
        nums = [int(x) for x in m.group(3).split(",")[1:]]
        r = an.D.decode(unescape(m.group(1)), int(m.group(2)), *nums)
        out.append((int(m.group(2)), r))
    return out
