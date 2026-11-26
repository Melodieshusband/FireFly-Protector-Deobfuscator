import re

from .emulate import DEFAULT_SYMBOLIC, EmulationResult, install_symbolic, render_events, run_in_big_stack
from .luastd import Interpreter
from .luavm import LuaError, LuaFunction, LuaTable, StepLimit, rawget, rawset, set_budget
from .v2 import Analysis2, Record, V2Error, decode_string

SESSION_RECORD = 2
SESSION_TOKEN = 8
SESSION_DECODE_LONG = 7
SESSION_DECODE = 18
SESSION_NOISE = 17

HOST_NAME = "__v2host"


def function_params(toks, fn_idx, bp):
    open_idx = fn_idx + 1
    close = bp[open_idx]
    return [toks[k].val for k in range(open_idx + 1, close) if toks[k].val != ","]


def body_statements(toks, ep, bp, first, last):
    starts = []
    k = first
    while k < last:
        t = toks[k]
        if t.val == "return":
            starts.append((k, "return"))
            break
        if t.kind == "name" and k + 2 < last and toks[k + 1].val == "=" and toks[k + 2].val != "=":
            if k == 0 or toks[k - 1].val not in (".", ":"):
                starts.append((k, t.val))
        if t.val in ("(", "[", "{"):
            k = bp[k] + 1
            continue
        if t.val in ("function", "do", "if", "while", "for", "repeat") and k in ep:
            k = ep[k] + 1
            continue
        k += 1
    out = []
    for i, (a, name) in enumerate(starts):
        b = starts[i + 1][0] if i + 1 < len(starts) else last
        out.append((name, a, b))
    return out


def split_args(toks, bp, open_idx):
    close = bp[open_idx]
    parts = []
    seg = open_idx + 1
    k = open_idx + 1
    while k < close:
        t = toks[k]
        if t.val in ("(", "[", "{"):
            k = bp[k] + 1
            continue
        if t.val == "function":
            k += 1
            continue
        if t.val == ",":
            parts.append((seg, k))
            seg = k + 1
        k += 1
    parts.append((seg, close))
    return parts


class VmLayout:
    def __init__(self, an):
        self.an = an
        src, toks, ep, bp = an.src, an.toks, an.ep, an.bp
        self.src = src
        enclosing = [i for i, t in enumerate(toks) if t.val == "function" and i in ep and i < an.table_open < ep[i]]
        if len(enclosing) < 2:
            raise V2Error("VM function structure not recognised")
        enclosing.sort()
        self.h_fn = enclosing[-1]
        self.outer_fn = None
        for i in enclosing:
            if len(function_params(toks, i, bp)) >= 20:
                self.outer_fn = i
        if self.outer_fn is None:
            raise V2Error("outer VM function not found")
        self.outer_params = function_params(toks, self.outer_fn, bp)
        outer_end = ep[self.outer_fn]
        first = bp[self.outer_fn + 1] + 1
        self.statements = body_statements(toks, ep, bp, first, outer_end)
        self.arg_parts = None
        call_open = self.outer_fn - 1
        if toks[call_open].val == "(" and toks[bp[call_open] + 1].val == "(":
            self.arg_parts = split_args(toks, bp, bp[call_open] + 1)
        if self.arg_parts is None:
            raise V2Error("outer VM call not found")
        self.h_params = function_params(toks, self.h_fn, bp)
        if len(self.h_params) != 4:
            raise V2Error("unexpected executor signature")
        self.h_end = ep[self.h_fn]
        self.regs = self.find_registers()
        self.session = self.find_session_name()
        self.heap_name, self.sentinel_name = self.find_heap_names()
        self.tail = self.find_tail()

    def text(self, a, b):
        toks = self.an.toks
        return self.src[toks[a].start:toks[b - 1].end]

    def find_registers(self):
        toks = self.an.toks
        k = bp_first = self.an.bp[self.h_fn + 1] + 1
        best = None
        while k < self.an.table_open:
            if toks[k].val == "local":
                j = k + 1
                names = []
                while toks[j].kind == "name":
                    names.append(toks[j].val)
                    j += 1
                    if toks[j].val == ",":
                        j += 1
                    else:
                        break
                if len(names) >= 10 and toks[j].val != "=":
                    best = names
                    break
            k += 1
        if best is None:
            raise V2Error("register list not found")
        return best

    def find_session_name(self):
        m = re.search(r"(\w+)\[%d\]=" % SESSION_NOISE, self.text(self.h_fn, self.h_end + 1))
        if m is None:
            raise V2Error("session table not found")
        if m.group(1) not in self.regs:
            raise V2Error("session table is not a register")
        return m.group(1)

    def find_heap_names(self):
        toks = self.an.toks
        m = re.search(r"local (\w+),(\w+)=(\w+)\[1\],\3\[2\]", self.text(self.h_fn, self.h_end + 1))
        if m is None:
            raise V2Error("heap declaration not found")
        return m.group(1), m.group(2)

    def find_tail(self):
        body = self.text(self.h_fn, self.h_end + 1)
        wname = self.h_params[1]
        m = re.search(r"%s\[%s-(\w+)\[4\]\]=nil" % (re.escape(self.heap_name), re.escape(wname)), body)
        if m is None:
            raise V2Error("executor epilogue not found")
        tail = body[m.start():]
        if not tail.rstrip().endswith("end"):
            raise V2Error("executor epilogue malformed")
        tail = tail.rstrip()[:-3]
        return tail

    def kept_statements(self):
        drop = {"h", "s", "u", "SS", "US"}
        kept = []
        for name, a, b in self.statements:
            if name == "return":
                continue
            if name in drop:
                continue
            kept.append(self.text(a, b))
        return kept

    def final_return(self):
        for name, a, b in self.statements:
            if name == "return":
                return self.text(a, b)
        raise V2Error("VM entry return not found")

    def block_table_text(self):
        toks = self.an.toks
        return self.src[toks[self.an.table_open].start:toks[self.an.bp[self.an.table_open]].end]

    def host_source(self):
        p = self.h_params
        regs = ",".join(self.regs)
        sess = self.session
        heap, sent = self.heap_name, self.sentinel_name
        lines = []
        lines.append("return function(%s)" % ",".join(self.outer_params))
        for stmt in self.kept_statements():
            lines.append(stmt)
        lines.append("%s=function(%s)" % (self._h_name(), ",".join(p)))
        lines.append("local Kx={}")
        lines.append("local %s,%s=o[1],o[2]" % (heap, sent))
        lines.append("local __hc=%s local __hq=(o[3]+o[5])%%2147483647 o[3]=__hq %s[__hq]=__hc==nil and %s or __hc %s=__hq+o[4]" % (p[1], heap, sent, p[1]))
        lines.append("local %s" % regs)
        lines.append("%s={}" % sess)
        lines.append("%s[%d]=%s.noop" % (sess, SESSION_NOISE, HOST_NAME))
        lines.append("%s[%d]=function(...)return %s.decode(%s[%d],...)end" % (sess, SESSION_DECODE, HOST_NAME, sess, SESSION_RECORD))
        lines.append("%s[%d]=%s[%d]" % (sess, SESSION_DECODE_LONG, sess, SESSION_DECODE))
        lines.append("%s[%d]=%s.token" % (sess, SESSION_TOKEN, HOST_NAME))
        lines.append("local __blocks=%s" % self.block_table_text())
        jr = self._find_name_role()
        lines.append("while %s do" % p[0])
        lines.append("%s=%s.record(%s)" % (jr["record"], HOST_NAME, p[0]))
        lines.append("%s=%s %s=%s[%s[4]]" % (jr["opsrc_holder"], jr["opsrc"], jr["ops"], jr["opsrc_holder"], jr["record"]))
        lines.append("__blocks[%s[1]]()" % jr["record"])
        lines.append("end")
        lines.append(self.tail)
        lines.append("end")
        lines.append(self.final_return())
        lines.append("end")
        return "\n".join(lines)

    def _h_name(self):
        toks = self.an.toks
        for name, a, b in self.statements:
            if a <= self.h_fn < b:
                return name
        raise V2Error("executor name not found")

    def _find_name_role(self):
        body = self.text(self.h_fn, self.h_end + 1)
        m = re.search(r"while %s do .*?(\w+)=(\w+) (\w+)=\1\[(\w+)\[4\]\](\w+)\[(\w+)\[1\]\]\(\)" % re.escape(self.h_params[0]), body, re.S)
        if m is None:
            raise V2Error("dispatch loop not recognised")
        return {
            "opsrc_holder": m.group(1),
            "opsrc": m.group(2),
            "ops": m.group(3),
            "record": m.group(4),
        }

    def argument_sources(self):
        toks = self.an.toks
        out = []
        for a, b in self.arg_parts:
            text = self.src[toks[a].start:toks[b - 1].end] if b > a else "nil"
            if toks[a].val == "(" and toks[a + 1].val == "function":
                text = "nil"
            out.append(text)
        return out


def read_banner(src):
    m = re.match(r'\s*\("([^"]*)"\):gsub\("\.\+",function\((\w+)\)(\w+)=\2 end\)', src)
    if m is None:
        return None, None
    return m.group(3), m.group(1)


def static_run(src, assume=(), strict=False, step_limit=50000000):
    result = EmulationResult()
    an = Analysis2(src)
    layout = VmLayout(an)
    host_text = layout.host_source()
    arg_text = "return " + ",".join(layout.argument_sources())
    holder = {"an": an}

    def work():
        interp = Interpreter()
        names = set(assume) if strict else set(DEFAULT_SYMBOLIC) | set(assume)
        install_symbolic(interp, names)
        banner_name, banner = read_banner(src)
        if banner_name is not None:
            rawset(interp.G, banner_name, banner)
        host = LuaTable()
        record_cache = {}

        def record(args):
            key = args[0]
            rec = an.records.get(key) if type(key) in (int, float) else None
            if rec is None:
                raise LuaError("VM state %s has no record" % (key,))
            t = record_cache.get(rec.key)
            if t is None:
                t = LuaTable()
                for i, v in enumerate((rec.block, rec.f2, rec.f3, rec.op_set, rec.check, rec.f6, rec.f7), 1):
                    rawset(t, i, v)
                record_cache[rec.key] = t
            return [t]

        def decode(args):
            jrec = args[0]
            if type(jrec) is not LuaTable or len(args) < 5:
                raise LuaError("string decode called without a state")
            text, mode, nt, f5 = args[1], args[2], args[3], args[4]
            fields = [rawget(jrec, i) for i in range(1, 8)]
            rec = Record(0, tuple(fields))
            val = decode_string(text.encode("latin-1"), mode, nt, f5, rec, an.params)
            if val is None:
                raise LuaError("string decode failed")
            return [val]

        rawset(host, "record", LuaFunction(record, "record", True))
        rawset(host, "decode", LuaFunction(decode, "decode", True))
        rawset(host, "noop", LuaFunction(lambda a: [False], "noop", True))
        rawset(host, "token", LuaFunction(lambda a: [0], "token", True))
        rawset(interp.G, HOST_NAME, host)
        set_budget(step_limit)
        holder["interp"] = interp
        try:
            builder = interp.compile(host_text).fn([])[0]
            argfn = interp.compile(arg_text)
            values = argfn.fn([])
            padded = list(values) + [None] * (len(layout.outer_params) - len(values))
            holder["ret"] = builder.fn(padded)
        except LuaError as e:
            holder["error"] = e.value
        except StepLimit:
            holder["limit"] = True
        except RecursionError:
            holder["error"] = "stack overflow"
        except (SyntaxError, IndexError, KeyError) as e:
            holder["syntax"] = "%s: %s" % (type(e).__name__, e)

    run_in_big_stack(work)
    if "syntax" in holder:
        raise V2Error(holder["syntax"])
    interp = holder.get("interp")
    if interp is not None:
        result.lines = render_events(interp.events)
        result.loaded = list(interp.loaded)
    result.error = holder.get("error")
    result.limit_hit = bool(holder.get("limit"))
    result.returned = holder.get("ret") or []
    result.an = an
    return result
