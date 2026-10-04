import re
from .emitter import Emitter, q
from .evaluator import Evaluator
from .folding import is_const, mk_and, mk_bin, mk_or, mk_un
from .refine import Inliner, MAKE, Namer, mask, promote_cells, rename, unmask
from .values import Clo, Frame, LuaErr, Obj, Stuck

SKIP = object()
TOP = object()
IMPURE_KINDS = ("call", "index", "global", "cellval", "alloc", "closure")
PURE_BUILTIN_PREFIX = ("string.", "math.")
PURE_BUILTINS = ("select", "type", "tostring", "tonumber", "unpack", "table.unpack", "table.pack", "table.concat", "rawget", "rawequal", "rawlen", "next")


class Unstructured(Exception):
    pass


def okey(v):
    t = type(v)
    if v is None or t in (bool, int, float, str):
        if t is float and v != v:
            return TOP
        return (t.__name__, v)
    return TOP


class Observer(Evaluator):
    def __init__(self, an, **kw):
        Evaluator.__init__(self, an, **kw)
        self.main_fr = None
        self.tmp_obs = {}
        self.reg_obs = {}
        self.next_obs = {}
        self.visit = {}
        self.callee_obs = {}

    def note(self, d, k, v):
        c = okey(v)
        old = d.get(k, SKIP)
        if old is SKIP:
            d[k] = c
        elif old is TOP or c is TOP or old != c:
            d[k] = TOP

    def run_main(self, entry):
        an = self.an
        fr = Frame([], [])
        self.main_fr = fr
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

    def exec_let(self, s, fr):
        if fr is self.main_fr and s[2][0] == "call":
            f = self.ev(s[2][1], fr)
            if type(f) is Clo:
                self.callee_obs[s[1]] = f
        Evaluator.exec_let(self, s, fr)

    def exec_state(self, r, fr):
        res = Evaluator.exec_state(self, r, fr)
        if fr is self.main_fr:
            self.record(r, fr, res)
        return res

    def record(self, r, fr, res):
        k = r["key"]
        self.visit[k] = self.visit.get(k, 0) + 1
        for tid, v in fr.tmps.items():
            self.note(self.tmp_obs, tid, v)
        for tid, m in fr.multi.items():
            self.note(self.tmp_obs, tid, m[0] if m else None)
        skip = (self.an.disp["state"], self.an.disp["ret"])
        for n in r["writes"]:
            if n not in skip:
                self.note(self.reg_obs, (k, n), fr.regs.get(n))
        self.next_obs.setdefault(k, set()).add(res[1] if res[0] == "goto" else None)


class Node:
    __slots__ = ("key", "stmts", "writes", "term", "ret")

    def __init__(self, key, stmts, writes, term, ret):
        self.key = key
        self.stmts = stmts
        self.writes = writes
        self.term = term
        self.ret = ret


def mapx(e, f):
    r = f(e)
    if r is not None:
        return r
    k = e[0]
    if k == "bin":
        return mk_bin(e[1], mapx(e[2], f), mapx(e[3], f))
    if k == "un":
        return mk_un(e[1], mapx(e[2], f))
    if k == "and":
        return mk_and(mapx(e[1], f), mapx(e[2], f))
    if k == "or":
        return mk_or(mapx(e[1], f), mapx(e[2], f))
    if k == "table":
        return ("table", [(it[0],) + tuple(mapx(x, f) for x in it[1:]) for it in e[1]])
    if k in ("first", "unpack", "multi", "cellref"):
        inner = mapx(e[1], f)
        if k == "first" and is_const(inner):
            return inner
        return (k, inner)
    if k == "select":
        return ("select", e[1], mapx(e[2], f))
    if k == "call":
        return ("call", mapx(e[1], f), [mapx(a, f) for a in e[2]])
    if k == "index":
        return ("index", mapx(e[1], f), mapx(e[2], f))
    if k == "global":
        return ("global", mapx(e[1], f))
    if k == "cellval":
        return ("cellval", mapx(e[1], f))
    if k == "closure":
        return ("closure", e[1], [mapx(c, f) for c in e[2]], e[3])
    return e


def mapstmt(s, f):
    k = s[0]
    if k == "let":
        return ("let", s[1], mapx(s[2], f))
    if k == "cellset":
        return ("cellset", mapx(s[1], f), mapx(s[2], f))
    if k == "setglobal":
        return ("setglobal", mapx(s[1], f), mapx(s[2], f))
    if k == "setindex":
        return ("setindex", mapx(s[1], f), mapx(s[2], f), mapx(s[3], f))
    return s


def walk(e, fn):
    if isinstance(e, tuple):
        if e and isinstance(e[0], str):
            fn(e)
        for x in e:
            walk(x, fn)
    elif isinstance(e, list):
        for x in e:
            walk(x, fn)


def node_exprs(nd):
    out = []
    for s in nd.stmts:
        out.extend(s[1:] if s[0] != "let" else [s[2]])
    out.extend(nd.writes.values())
    if nd.term[0] == "branch":
        out.append(nd.term[1])
    if nd.ret is not None:
        out.append(nd.ret)
    return out


def regs_used(exprs):
    s = set()

    def f(e):
        if e[0] == "reg" and len(e) == 2 and isinstance(e[1], str):
            s.add(e[1])

    for e in exprs:
        walk(e, f)
    return s


def tmps_used(exprs):
    cnt = {}

    def f(e):
        if e[0] == "tmp" and len(e) == 2:
            cnt[e[1]] = cnt.get(e[1], 0) + 1

    for e in exprs:
        walk(e, f)
    return cnt


def succs_of(nd):
    t = nd.term
    if t[0] == "goto":
        return [t[1]]
    if t[0] == "branch":
        return [x for x in (t[2], t[3]) if x is not None]
    return []


class Graph:
    def __init__(self, an, key):
        self.an = an
        self.key = key
        self.nodes = {}
        self.pure_tids = set()
        self.hoist = []
        stack = [key]
        skip = (an.disp["state"], an.disp["ret"])
        while stack:
            k = stack.pop()
            if k in self.nodes:
                continue
            r = an.lift(k)
            nx = r["next"]
            if nx[0] == "goto":
                term = ("goto", nx[1])
            elif nx[0] == "ret":
                term = ("ret",)
            else:
                term = ("branch", nx[1], nx[2][1] if nx[2][0] == "goto" else None, nx[3][1] if nx[3][0] == "goto" else None)
            writes = {n: v for n, v in r["writes"].items() if n not in skip}
            self.nodes[k] = Node(k, list(r["stmts"]), writes, term, r["ret"])
            stack.extend(succs_of(self.nodes[k]))

    def preds(self):
        p = {k: [] for k in self.nodes}
        for k, nd in self.nodes.items():
            for s in succs_of(nd):
                p[s].append(k)
        return p

    def cycles(self):
        index = {}
        low = {}
        onstack = set()
        stack = []
        res = set()
        counter = [0]

        def strong(v):
            work = [(v, iter(succs_of(self.nodes[v])))]
            index[v] = low[v] = counter[0]
            counter[0] += 1
            stack.append(v)
            onstack.add(v)
            while work:
                node, it = work[-1]
                adv = False
                for w in it:
                    if w not in index:
                        index[w] = low[w] = counter[0]
                        counter[0] += 1
                        stack.append(w)
                        onstack.add(w)
                        work.append((w, iter(succs_of(self.nodes[w]))))
                        adv = True
                        break
                    elif w in onstack:
                        low[node] = min(low[node], index[w])
                if adv:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        onstack.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    if len(comp) > 1 or node in succs_of(self.nodes[node]):
                        res.update(comp)

        for k in self.nodes:
            if k not in index:
                strong(k)
        return res

    def prune(self):
        seen = set()
        stack = [self.key]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            stack.extend(succs_of(self.nodes[k]))
        for k in list(self.nodes):
            if k not in seen:
                del self.nodes[k]

    def fold_branches(self):
        changed = False
        for nd in self.nodes.values():
            t = nd.term
            if t[0] != "branch":
                continue
            c = t[1]
            if t[2] == t[3]:
                nd.term = ("goto", t[2]) if t[2] is not None else ("ret",)
                if nd.term[0] == "ret" and nd.ret is None:
                    nd.ret = ("table", [])
                changed = True
            elif is_const(c):
                tgt = t[2] if (c[1] is not None and c[1] is not False) else t[3]
                if tgt is None:
                    nd.term = ("ret",)
                    nd.ret = ("table", [])
                else:
                    nd.term = ("goto", tgt)
                changed = True
        return changed

    def merge_chains(self):
        changed = False
        again = True
        while again:
            again = False
            preds = self.preds()
            for k in list(self.nodes):
                a = self.nodes.get(k)
                if a is None or a.term[0] != "goto":
                    continue
                b_key = a.term[1]
                if b_key == k or b_key == self.key or len(preds.get(b_key, [])) != 1:
                    continue
                b = self.nodes[b_key]
                wr = a.writes

                def f(e, wr=wr):
                    if e[0] == "reg" and len(e) == 2 and e[1] in wr:
                        return wr[e[1]]
                    return None

                stmts = a.stmts + [mapstmt(s, f) for s in b.stmts]
                writes = dict(wr)
                for n, v in b.writes.items():
                    writes[n] = mapx(v, f)
                for n in list(writes):
                    if writes[n] == ("reg", n):
                        del writes[n]
                term = b.term
                if term[0] == "branch":
                    term = ("branch", mapx(term[1], f), term[2], term[3])
                a.stmts = stmts
                a.writes = writes
                a.term = term
                a.ret = mapx(b.ret, f) if b.ret is not None else None
                del self.nodes[b_key]
                changed = True
                again = True
                break
        return changed

    def pure_call(self, e):
        fn = e[1]
        if fn[0] == "builtin":
            name = fn[1]
            return name in PURE_BUILTINS or name.startswith(PURE_BUILTIN_PREFIX)
        return False

    def dce(self):
        changed = False
        live_in = {k: set() for k in self.nodes}
        order = list(self.nodes)
        moved = True
        while moved:
            moved = False
            for k in reversed(order):
                nd = self.nodes[k]
                out = set()
                for s in succs_of(nd):
                    out |= live_in[s]
                keep = {n: v for n, v in nd.writes.items() if n in out}
                exprs = []
                for s in nd.stmts:
                    exprs.extend(s[1:] if s[0] != "let" else [s[2]])
                exprs.extend(keep.values())
                if nd.term[0] == "branch":
                    exprs.append(nd.term[1])
                if nd.ret is not None:
                    exprs.append(nd.ret)
                li = regs_used(exprs) | (out - set(nd.writes))
                if li != live_in[k]:
                    live_in[k] = li
                    moved = True
        self.live = live_in
        for k, nd in self.nodes.items():
            out = set()
            for s in succs_of(nd):
                out |= live_in[s]
            for n in list(nd.writes):
                if n not in out:
                    del nd.writes[n]
                    changed = True
            while True:
                cnt = tmps_used(node_exprs(nd))
                removed = False
                for i, s in enumerate(nd.stmts):
                    if s[0] != "let" or cnt.get(s[1], 0) > 0:
                        continue
                    e = s[2]
                    if s[1] in self.hoist:
                        continue
                    if e[0] in ("index", "global", "cellval", "alloc", "closure") or (e[0] == "call" and (self.pure_call(e) or s[1] in self.pure_tids)):
                        del nd.stmts[i]
                        removed = True
                        changed = True
                        break
                if not removed:
                    break
        return changed

    def alias_cells(self):
        cyc = self.cycles()
        names = set(n for n, _ in self.an.prologue)
        writers = {}
        readers = {}
        for k, nd in self.nodes.items():
            for r, v in nd.writes.items():
                writers.setdefault(r, []).append((k, v))
            for r in regs_used(node_exprs(nd)):
                readers.setdefault(r, set()).add(k)
        for r, ws in sorted(writers.items()):
            if len(ws) != 1 or r in names:
                continue
            k, v = ws[0]
            if v[0] != "tmp" or k in cyc:
                continue
            nd = self.nodes[k]
            let = None
            for s in nd.stmts:
                if s[0] == "let" and s[1] == v[1]:
                    let = s
            if let is None or let[2][0] != "alloc":
                continue
            rd = readers.get(r, set())
            if not rd or k in rd:
                continue
            seen = set()
            stack = [self.key]
            while stack:
                x = stack.pop()
                if x in seen or x == k:
                    continue
                seen.add(x)
                stack.extend(succs_of(self.nodes[x]))
            if any(x in seen for x in rd):
                continue
            rep = ("tmp", v[1])

            def f(e, r=r, rep=rep):
                if e[0] == "reg" and len(e) == 2 and e[1] == r:
                    return rep
                return None

            for x in rd:
                n2 = self.nodes[x]
                n2.stmts = [mapstmt(s, f) for s in n2.stmts]
                n2.writes = {n: mapx(val, f) for n, val in n2.writes.items()}
                if n2.term[0] == "branch":
                    n2.term = ("branch", mapx(n2.term[1], f), n2.term[2], n2.term[3])
                if n2.ret is not None:
                    n2.ret = mapx(n2.ret, f)
            del nd.writes[r]
            if v[1] not in self.hoist:
                self.hoist.append(v[1])

    def live_entry(self):
        self.dce()
        return set(self.live.get(self.key, set()))

    def simplify(self):
        for _ in range(50):
            c = False
            c |= self.fold_branches()
            self.prune()
            c |= self.merge_chains()
            c |= self.dce()
            if not c:
                break


def apply_obs(g, ev, complete):
    cyc = g.cycles()
    multi = set()

    def fm(e):
        if e[0] == "multi" and isinstance(e[1], tuple) and e[1][0] == "tmp":
            multi.add(e[1][1])

    for nd in g.nodes.values():
        for e in node_exprs(nd):
            walk(e, fm)
    for k, nd in g.nodes.items():
        if ev.visit.get(k, 0) == 0:
            continue
        if not complete and k in cyc:
            continue
        cmap = {}
        for s in nd.stmts:
            if s[0] == "let" and s[1] not in multi:
                o = ev.tmp_obs.get(s[1], SKIP)
                if o is not SKIP and o is not TOP:
                    cmap[s[1]] = ("const", o[1])

        def f(e, cmap=cmap):
            if e[0] == "tmp" and e[1] in cmap:
                return cmap[e[1]]
            return None

        nd.stmts = [mapstmt(s, f) for s in nd.stmts]
        nd.writes = {n: mapx(v, f) for n, v in nd.writes.items()}
        for n in list(nd.writes):
            o = ev.reg_obs.get((k, n), SKIP)
            if o is not SKIP and o is not TOP:
                nd.writes[n] = ("const", o[1])
        if nd.term[0] == "branch":
            t = nd.term
            cond = mapx(t[1], f)
            nx = ev.next_obs.get(k, set())
            if len(nx) == 1:
                tgt = next(iter(nx))
                if tgt is None:
                    nd.term = ("ret",)
                    nd.ret = ("table", [])
                else:
                    nd.term = ("goto", tgt)
            else:
                nd.term = ("branch", cond, t[2], t[3])
        if nd.ret is not None:
            nd.ret = mapx(nd.ret, f)


def impure_node(e):
    return e[0] in IMPURE_KINDS


def first_ok(e, tid):
    found = [False]
    bad = [False]

    def go(x):
        if found[0] or bad[0]:
            return
        if not isinstance(x, tuple) or not x or not isinstance(x[0], str):
            if isinstance(x, list):
                for y in x:
                    go(y)
            return
        k = x[0]
        if k == "tmp":
            if x[1] == tid:
                found[0] = True
            return
        if k in ("and", "or"):
            go(x[1])
            if found[0] or bad[0]:
                return
            if tid in tmps_used([x[2]]):
                bad[0] = True
                return
            return
        if k == "table":
            for it in x[1]:
                for y in it[1:]:
                    go(y)
                    if found[0] or bad[0]:
                        return
            return
        if k == "closure":
            for y in x[2]:
                go(y)
                if found[0] or bad[0]:
                    return
            if impure_node(x) and not found[0]:
                bad[0] = True
            return
        for y in x[1:]:
            if isinstance(y, (tuple, list)):
                go(y)
                if found[0] or bad[0]:
                    return
        if impure_node(x) and not found[0]:
            bad[0] = True

    go(e)
    return found[0] and not bad[0]


def replace_tmp(e, tid, rep):
    def f(x):
        if x[0] == "tmp" and x[1] == tid:
            return rep
        return None

    return mapx(e, f)


def fold_block(nd, multi, hoist=()):
    while True:
        cnt = tmps_used(node_exprs(nd))
        done = True
        for i, s in enumerate(nd.stmts):
            if s[0] != "let" or cnt.get(s[1], 0) != 1 or s[1] in hoist:
                continue
            tid = s[1]
            if i + 1 < len(nd.stmts):
                c = nd.stmts[i + 1]
                parts = c[1:] if c[0] != "let" else [c[2]]
                if tid not in tmps_used(parts):
                    continue
                if not first_ok(("seq", parts), tid):
                    continue
                nd.stmts[i + 1] = mapstmt(c, lambda x, tid=tid, rep=s[2]: rep if (x[0] == "tmp" and x[1] == tid) else None)
                del nd.stmts[i]
                done = False
                break
            if nd.ret is not None and tid in tmps_used([nd.ret]) and first_ok(nd.ret, tid):
                nd.ret = replace_tmp(nd.ret, tid, s[2])
                del nd.stmts[i]
                done = False
                break
            if not nd.writes and nd.term[0] == "branch" and tid in tmps_used([nd.term[1]]) and first_ok(nd.term[1], tid):
                nd.term = ("branch", replace_tmp(nd.term[1], tid, s[2]), nd.term[2], nd.term[3])
                del nd.stmts[i]
                done = False
                break
            if len(nd.writes) == 1 and nd.term[0] == "goto":
                n, v = next(iter(nd.writes.items()))
                if tid in tmps_used([v]) and first_ok(v, tid):
                    nd.writes[n] = replace_tmp(v, tid, s[2])
                    del nd.stmts[i]
                    done = False
                    break
        if done:
            return


TERM = object()
EXIT = "exit"


class Printer(Emitter):
    def __init__(self, an):
        self.an = an
        self.multi = set()

    def prepare(self, g):
        self.multi = set()
        self.hoist = set(g.hoist)

        def f(e):
            if e[0] == "multi" and isinstance(e[1], tuple) and e[1][0] == "tmp":
                self.multi.add(e[1][1])

        for nd in g.nodes.values():
            for e in node_exprs(nd):
                walk(e, f)

    def x(self, e):
        if e[0] in ("call", "index", "global", "cellval", "alloc", "closure"):
            return self.let_rhs(e)
        return Emitter.x(self, e)

    def prefix(self, e):
        s = self.x(e)
        if e[0] in ("reg", "tmp", "index", "call", "global", "cellval", "builtin", "cap", "env", "first", "arg"):
            return s
        if s.startswith("("):
            return s
        return "(%s)" % s

    def let_rhs(self, e):
        k = e[0]
        if k == "call":
            return "%s(%s)" % (self.prefix(e[1]), self.args(e[2]))
        if k == "index":
            return "%s[%s]" % (self.prefix(e[1]), self.x(e[2]))
        if k == "cellval":
            return "%s.v" % self.prefix(e[1])
        return Emitter.let_rhs(self, e)

    def stmt_lines(self, nd):
        out = []
        cnt = tmps_used(node_exprs(nd))
        for s in nd.stmts:
            k = s[0]
            if k == "let":
                if s[1] in self.hoist:
                    continue
                rhs = self.let_rhs(s[2])
                if s[2][0] == "call" and cnt.get(s[1], 0) == 0:
                    out.append((";" if rhs.startswith("(") else "") + rhs)
                elif s[1] in self.multi and s[2][0] == "call":
                    out.append("local %s = table.pack(%s)" % (self.tn(s[1]), rhs))
                else:
                    out.append("local %s = %s" % (self.tn(s[1]), rhs))
            elif k == "cellset":
                out.append("%s.v = %s" % (self.prefix(s[1]), self.x(s[2])))
            elif k == "setglobal":
                out.append("_ENV[%s] = %s" % (self.x(s[1]), self.x(s[2])))
            elif k == "setindex":
                out.append("%s[%s] = %s" % (self.prefix(s[1]), self.x(s[2]), self.x(s[3])))
        return out

    def write_lines(self, nd):
        ws = sorted(nd.writes.items())
        if not ws:
            return []
        targets = set(n for n, _ in ws)
        conflict = any((regs_used([v]) & targets) - {n} for n, v in ws)
        if conflict and len(ws) > 1:
            return ["%s = %s" % (", ".join("r_" + n for n, _ in ws), ", ".join(self.x(v) for _, v in ws))]
        return ["r_%s = %s" % (n, self.x(v)) for n, v in ws]

    def ret_text(self, nd):
        r = nd.ret
        if r is not None and r[0] == "table":
            vals = ", ".join(self.x(i[1]) for i in r[1] if i[0] == "pos")
            return vals or None
        return None


class Loop:
    def __init__(self, header, body, exit, outer):
        self.header = header
        self.body = body
        self.exit = exit
        self.outer = outer


class Struct:
    def __init__(self, g, pr):
        self.g = g
        self.pr = pr
        self.nodes = g.nodes
        self.analyse()

    def analyse(self):
        g = self.g
        entry = g.key
        seen = set()
        post = []
        stack = [(entry, iter(succs_of(self.nodes[entry])))]
        seen.add(entry)
        while stack:
            n, it = stack[-1]
            adv = False
            for s in it:
                if s not in seen:
                    seen.add(s)
                    stack.append((s, iter(succs_of(self.nodes[s]))))
                    adv = True
                    break
            if not adv:
                post.append(n)
                stack.pop()
        rpo = list(reversed(post))
        self.idx = {n: i for i, n in enumerate(rpo)}
        preds = g.preds()
        idom = {entry: entry}

        def inter(a, b):
            while a != b:
                while self.idx[a] > self.idx[b]:
                    a = idom[a]
                while self.idx[b] > self.idx[a]:
                    b = idom[b]
            return a

        moved = True
        while moved:
            moved = False
            for n in rpo[1:]:
                ps = [p for p in preds[n] if p in idom]
                new = ps[0]
                for p in ps[1:]:
                    new = inter(p, new)
                if idom.get(n) != new:
                    idom[n] = new
                    moved = True

        def dom(a, b):
            while True:
                if a == b:
                    return True
                if b == entry:
                    return False
                b = idom[b]

        back = {}
        for u in rpo:
            for v in succs_of(self.nodes[u]):
                if self.idx[v] <= self.idx[u]:
                    if not dom(v, u):
                        raise Unstructured("irreducible")
                    back.setdefault(v, []).append(u)
        self.loops = {}
        for h, srcs in back.items():
            body = {h}
            work = list(srcs)
            while work:
                x = work.pop()
                if x in body:
                    continue
                body.add(x)
                work.extend(preds[x])
            outside = []
            for n in body:
                for s in succs_of(self.nodes[n]):
                    if s not in body and s not in outside:
                        outside.append(s)
            exit = None
            if len(outside) == 1:
                exit = outside[0]
            elif len(outside) > 1:
                reach = {}
                for e in outside:
                    r = set()
                    st = [e]
                    while st:
                        y = st.pop()
                        if y in r:
                            continue
                        r.add(y)
                        st.extend(succs_of(self.nodes[y]))
                    reach[e] = r
                cands = [x for x in outside if all(x == e or x in reach[e] for e in outside)]
                if len(cands) != 1:
                    cands = [x for x in outside if any(self.nodes[y].term[0] != "ret" for y in reach[x])]
                if len(cands) != 1:
                    raise Unstructured("multiple loop exits")
                exit = cands[0]
            self.loops[h] = Loop(h, body, exit, None)
        allnodes = list(self.nodes)
        pd = {n: set(allnodes) | {EXIT} for n in allnodes}
        pd[EXIT] = {EXIT}
        backsrc = set()
        for srcs in back.values():
            backsrc.update(srcs)

        def psucc(n):
            nd = self.nodes[n]
            out = list(succs_of(nd))
            if nd.term[0] == "ret" or (nd.term[0] == "branch" and (nd.term[2] is None or nd.term[3] is None)) or n in backsrc:
                out.append(EXIT)
            return out

        moved = True
        while moved:
            moved = False
            for n in reversed(rpo):
                ss = psucc(n)
                new = None
                for s in ss:
                    new = set(pd[s]) if new is None else (new & pd[s])
                new = (new or set()) | {n}
                if new != pd[n]:
                    pd[n] = new
                    moved = True
        self.ipdom = {}
        for n in allnodes:
            cand = pd[n] - {n}
            best = None
            for c in cand:
                if best is None or len(pd[c]) > len(pd[best]):
                    best = c
            self.ipdom[n] = best

    def emit_node(self, n, stop, loop):
        nd = self.nodes[n]
        pr = self.pr
        t = nd.term
        out = []
        for line in pr.stmt_lines(nd):
            out.append(("raw", line))
        if t[0] == "ret":
            out.append(("return", pr.ret_text(nd)))
            return out, TERM
        if t[0] == "goto":
            for line in pr.write_lines(nd):
                out.append(("raw", line))
            return out, t[1]
        cond = t[1]
        if nd.writes and regs_used([cond]) & set(nd.writes):
            name = "c_%d" % (n % 100000)
            out.append(("raw", "local %s = %s" % (name, pr.x(cond))))
            condtext = name
            cexpr = None
        else:
            condtext = None
            cexpr = cond
        for line in pr.write_lines(nd):
            out.append(("raw", line))
        m = self.ipdom.get(n)
        merge = None
        if m is not None and m != EXIT and m in self.nodes:
            if loop is None or m in loop.body or m == loop.exit:
                merge = m
        arm_stop = merge if merge is not None else stop
        a = self.arm(t[2], arm_stop, loop)
        b = self.arm(t[3], arm_stop, loop)
        if condtext is None:
            condtext = pr.x(cexpr)
            neg = "(not %s)" % condtext if not condtext.startswith("(not ") else condtext[5:-1]
        else:
            neg = "(not %s)" % condtext
        if a or b:
            if not a:
                out.append(("if", neg, b, []))
            else:
                out.append(("if", condtext, a, b))
        return out, (merge if merge is not None else TERM)

    def arm(self, tgt, stop, loop):
        if tgt is None:
            return [("return", None)]
        return self.region(tgt, stop, loop)

    def region(self, n, stop, loop, entry=False):
        out = []
        first = True
        while True:
            if n is TERM or n is None:
                if n is None:
                    out.append(("return", None))
                return out
            if n == stop:
                return out
            if loop is not None:
                if n == loop.header and not (entry and first):
                    out.append(("continue",))
                    return out
                if n == loop.exit:
                    out.append(("break",))
                    return out
                lp = loop.outer
                while lp is not None:
                    if n == lp.header or n == lp.exit:
                        raise Unstructured("jump across loops")
                    lp = lp.outer
            if n in self.loops and not (entry and first and loop is not None and loop.header == n):
                L = self.loops[n]
                ln = Loop(n, L.body, L.exit, loop)
                body = self.region(n, None, ln, True)
                out.append(("while", "true", body))
                n = L.exit if L.exit is not None else TERM
                first = False
                continue
            first = False
            stmts, nxt = self.emit_node(n, stop, loop)
            out.extend(stmts)
            n = nxt


def negate(c):
    if c.startswith("(not "):
        return c[5:-1]
    return "(not %s)" % c


def strip_tail(body):
    while body and body[-1][0] == "continue":
        body.pop()
    if body and body[-1][0] == "if":
        k, c, a, b = body[-1]
        strip_tail(a)
        strip_tail(b)


def polish(stmts):
    out = []
    for s in stmts:
        if s[0] == "if":
            out.append(("if", s[1], polish(s[2]), polish(s[3])))
        elif s[0] == "while":
            body = polish(s[2])
            cond = s[1]
            i = 0
            while i < len(body) and body[i][0] == "raw":
                i += 1
            if cond == "true" and i < len(body) and body[i][0] == "if":
                _, c, a, b = body[i]
                if b == [("break",)] or a == [("break",)]:
                    if b == [("break",)]:
                        keep, c2 = a, c
                    else:
                        keep, c2 = b, negate(c)
                    if i == 0:
                        cond = c2
                        body = keep + body[1:]
                    else:
                        body = body[:i] + [("if", negate(c2), [("break",)], [])] + keep + body[i + 1:]
            strip_tail(body)
            out.append(("while", cond, body))
        else:
            out.append(s)
    return out


FOR_COND = re.compile(r"^local (c_\d+) = \(\((r_\w+) and \((\((r_\w+) \+ (r_\w+)\)) >= (r_\w+)\)\) or \(\(not (r_\w+)\) and \(\((r_\w+) \+ (r_\w+)\) <= (r_\w+)\)\)\)$")
FOR_INC = re.compile(r"^(r_\w+) = \((r_\w+) \+ (r_\w+)\)$")
ASSIGN = re.compile(r"^(r_\w+) = (.*)$")
NAME = re.compile(r"\br_\w+\b")


def tree_names(tree, cnt=None):
    if cnt is None:
        cnt = {}
    for s in tree:
        k = s[0]
        texts = []
        if k == "raw":
            texts = [s[1]]
        elif k == "return" and s[1]:
            texts = [s[1]]
        elif k in ("if", "while", "for"):
            texts = [s[1]]
        for t in texts:
            for n in NAME.findall(t):
                cnt[n] = cnt.get(n, 0) + 1
        if k == "if":
            tree_names(s[2], cnt)
            tree_names(s[3], cnt)
        elif k in ("while", "for"):
            tree_names(s[2], cnt)
    return cnt


def num_text(t):
    try:
        v = float(t)
    except ValueError:
        return None
    return v


def fmt_num(v):
    if v == int(v) and abs(v) < 2 ** 53:
        return str(int(v))
    return repr(v)


def flatten(stmts, acc=None):
    if acc is None:
        acc = []
    for s in stmts:
        k = s[0]
        if k == "raw":
            acc.append(s[1])
        elif k == "return":
            if s[1]:
                acc.append(s[1])
        elif k in ("if", "while", "for"):
            acc.append(s[1])
            if k == "if":
                flatten(s[2], acc)
                flatten(s[3], acc)
            else:
                flatten(s[2], acc)
    return acc


def first_is_write(lines, name):
    pat = re.compile(r"\b%s\b" % re.escape(name))
    for ln in lines:
        if not pat.search(ln):
            continue
        if " = " in ln and not ln.startswith(("if ", "while ", "for ", "return ")):
            lhs, rhs = ln.split(" = ", 1)
            names = [x.strip() for x in lhs.split(",")]
            if name in names and not pat.search(rhs):
                return True
        return False
    return True


def recover_for(stmts, cnt=None, after=None):
    after = after or []
    out = []
    for i, s in enumerate(stmts):
        following = flatten(stmts[i + 1:]) + after
        if s[0] == "if":
            out.append(("if", s[1], recover_for(s[2], None, flatten(s[3]) + following), recover_for(s[3], None, following)))
            continue
        if s[0] == "while":
            body = recover_for(s[2], None, flatten(s[2]) + following)
            conv = try_for(out, s[1], body, following)
            if conv is None:
                conv = try_generic_for(out, s[1], body, following)
            out.append(conv if conv is not None else ("while", s[1], body))
            continue
        out.append(s)
    return out


def try_for(out, cond, body, following):
    if cond != "true" or len(body) < 3:
        return None
    a, b, c = body[0], body[1], body[2]
    if a[0] != "raw" or b[0] != "raw" or c[0] != "if":
        return None
    m = FOR_COND.match(a[1])
    if not m:
        return None
    cname, K, I, S, L, K2, I2, S2, L2 = m.group(1), m.group(2), m.group(4), m.group(5), m.group(6), m.group(7), m.group(8), m.group(9), m.group(10)
    if not (K == K2 and I == I2 and S == S2 and L == L2):
        return None
    mi = FOR_INC.match(b[1])
    if not mi or mi.group(1) != I or mi.group(2) != I or mi.group(3) != S:
        return None
    if c[1] != "(not %s)" % cname or c[2] != [("break",)] or c[3] != []:
        return None
    rest = body[3:]
    inner = tree_names(rest)
    for n in (K, S, L):
        if inner.get(n, 0):
            return None
    if assigns_name(rest, I):
        return None
    inits = {}
    for idx in range(len(out) - 1, -1, -1):
        e = out[idx]
        if e[0] != "raw":
            continue
        ma = ASSIGN.match(e[1])
        if ma and ma.group(1) in (K, I, S, L) and ma.group(1) not in inits:
            inits[ma.group(1)] = (idx, ma.group(2))
    if len(inits) != 4:
        return None
    if inits[K][1] not in ("false", "true"):
        return None
    first = min(v[0] for v in inits.values())
    initidx = set(v[0] for v in inits.values())
    for idx in range(first, len(out)):
        if idx in initidx:
            continue
        if any(re.search(r"\b%s\b" % re.escape(n), t) for n in (K, I, S, L) for t in flatten([out[idx]])):
            return None
    for n in (K, I, S, L):
        if not first_is_write(following, n):
            return None
    i0 = num_text(inits[I][1])
    s0 = num_text(inits[S][1])
    if i0 is not None and s0 is not None:
        start = fmt_num(i0 + s0)
    else:
        start = "(%s + %s)" % (inits[I][1], inits[S][1])
    header = "%s = %s, %s" % (I, start, inits[L][1])
    if not (s0 is not None and s0 == 1):
        header += ", " + inits[S][1]
    for idx in sorted(initidx, reverse=True):
        del out[idx]
    return ("for", header, rest)


CALL_SINGLE = re.compile(r"^local (t\d+_\d+) = (r_\w+)\((r_\w+), (r_\w+)\)$")
CALL_PACK = re.compile(r"^local (t\d+_\d+) = table\.pack\((r_\w+)\((r_\w+), (r_\w+)\)\)$")
SEL1 = re.compile(r"^(r_\w+) = \(select\(1, (t\d+_\d+)\)\)$")
SEL2 = re.compile(r"^(r_\w+) = \(select\(2, (t\d+_\d+)\)\)$")
LOCALT = re.compile(r"^local (t\d+_\d+) = (.*)$")


def try_generic_for(out, cond, body, following):
    if cond != "true" or len(body) < 3 or body[0][0] != "raw":
        return None
    single = CALL_SINGLE.match(body[0][1])
    pack = CALL_PACK.match(body[0][1])
    if single:
        tA, F, S, C = single.groups()
        if len(body) < 3 or body[1][0] != "raw" or body[1][1] != "%s = %s" % (C, tA):
            return None
        if body[2] != ("if", "(not %s)" % tA, [("break",)], []):
            return None
        loopvars = [C]
        rest = body[3:]
    elif pack:
        tA, F, S, C = pack.groups()
        up = "table.unpack(%s, 1, %s.n)" % (tA, tA)
        found = {}
        i = 1
        while i < len(body) and body[i][0] == "raw":
            line = body[i][1]
            m1 = re.match(r"^(r_\w+) = %s$" % re.escape(up), line)
            m2 = re.match(r"^(r_\w+) = \(select\((\d+), %s\)\)$" % re.escape(up), line)
            if m1:
                found[1] = m1.group(1)
            elif m2:
                found[int(m2.group(2))] = m2.group(1)
            else:
                break
            i += 1
        if i >= len(body) or body[i] != ("if", "(not %s)" % up, [("break",)], []):
            return None
        n = len(found)
        if n == 0 or sorted(found) != list(range(1, n + 1)) or found[1] != C:
            return None
        loopvars = [found[k] for k in range(1, n + 1)]
        rest = body[i + 1:]
    else:
        return None
    inner = flatten(rest)
    for nm in (F, S):
        if any(re.search(r"\b%s\b" % re.escape(nm), t) for t in inner):
            return None
    if re.search(r"\b%s\b" % re.escape(tA), "\n".join(inner)):
        return None
    inits = {}
    for idx in range(len(out) - 1, -1, -1):
        e = out[idx]
        if e[0] != "raw":
            continue
        m = SEL1.match(e[1])
        if m and m.group(1) == F and "F" not in inits:
            inits["F"] = (idx, m.group(2))
            continue
        m = SEL2.match(e[1])
        if m and m.group(1) == S and "S" not in inits:
            inits["S"] = (idx, m.group(2))
            continue
        m = ASSIGN.match(e[1])
        if m and m.group(1) == S and "S0" not in inits and m.group(2) == "nil":
            inits["S0"] = (idx, None)
            continue
        if m and m.group(1) == C and "C" not in inits:
            inits["C"] = (idx, m.group(2))
    if "F" not in inits or "C" not in inits or ("S" not in inits and "S0" not in inits):
        return None
    if inits["C"][1] not in ("nil", "0"):
        return None
    tX = inits["F"][1]
    if "S" in inits and inits["S"][1] != tX:
        return None
    src = None
    for idx in range(len(out) - 1, -1, -1):
        e = out[idx]
        if e[0] != "raw":
            continue
        m = LOCALT.match(e[1])
        if m and m.group(1) == tX:
            src = (idx, m.group(2))
            break
    if src is None:
        return None
    if "S" not in inits and src[1].startswith(("pairs(", "ipairs(", "next")):
        return None
    used_idx = set(v[0] for v in inits.values()) | {src[0]}
    first = min(used_idx)
    for idx in range(first, len(out)):
        if idx in used_idx:
            continue
        if any(re.search(r"\b%s\b" % re.escape(n), t) for n in (F, S, C, tX) for t in flatten([out[idx]])):
            return None
    allafter = following
    for nm in [F, S] + loopvars:
        if not first_is_write(allafter, nm):
            return None
    if any(re.search(r"\b%s\b" % re.escape(tX), t) for t in following):
        return None
    for idx in sorted(used_idx, reverse=True):
        del out[idx]
    return ("for", "%s in %s" % (", ".join(loopvars), src[1]), rest)


def assigns_name(tree, name):
    for s in tree:
        if s[0] == "raw" and " = " in s[1]:
            lhs = s[1].split(" = ")[0]
            if name in [x.strip() for x in lhs.split(",")]:
                return True
        if s[0] == "if" and (assigns_name(s[2], name) or assigns_name(s[3], name)):
            return True
        if s[0] in ("while", "for") and assigns_name(s[2], name):
            return True
    return False


def render_tree(stmts, ind, out):
    pad = "    " * ind
    for s in stmts:
        k = s[0]
        if k == "raw":
            out.append(pad + s[1])
        elif k == "break":
            out.append(pad + "break")
        elif k == "continue":
            out.append(pad + "continue")
        elif k == "return":
            out.append(pad + ("return " + s[1] if s[1] else "return"))
        elif k == "while":
            out.append("%swhile %s do" % (pad, s[1]))
            render_tree(s[2], ind + 1, out)
            out.append(pad + "end")
        elif k == "for":
            out.append("%sfor %s do" % (pad, s[1]))
            render_tree(s[2], ind + 1, out)
            out.append(pad + "end")
        elif k == "if":
            out.append("%sif %s then" % (pad, s[1]))
            render_tree(s[2], ind + 1, out)
            els = s[3]
            while els and len(els) == 1 and els[0][0] == "if":
                e = els[0]
                out.append("%selseif %s then" % (pad, e[1]))
                render_tree(e[2], ind + 1, out)
                els = e[3]
            if els:
                out.append(pad + "else")
                render_tree(els, ind + 1, out)
            out.append(pad + "end")


class Decompiler:
    def __init__(self, an, ev, complete):
        self.an = an
        self.ev = ev
        self.complete = complete
        self.pr = Printer(an)
        self.graphs = {}
        self.fallbacks = []
        self.capvals = {}
        self.plain = {}
        self.arity = {}

    def classify(self, v):
        dec = self.ev.decoder
        if dec is None:
            return None
        if type(v) is Clo and v.key == dec.key:
            return "dec"
        if type(v) is Obj and v.mt is not None:
            idx = v.mt.d.get("__index")
            cache = dec.caps[0].d.get("v") if dec.caps and type(dec.caps[0]) is Obj else None
            if type(idx) is Obj and idx is cache:
                return "proxy"
        return None

    def known(self, e, env):
        k = e[0]
        if k == "reg":
            return env["regs"].get(e[1])
        if k == "cap":
            caps = env["caps"]
            i = e[1] - 1
            return caps[i] if caps and i < len(caps) else None
        if k == "tmp":
            return env["tmps"].get(e[1])
        if k == "cellval":
            c = self.known(e[1], env)
            if type(c) is Obj:
                return c.d.get("v")
        return None

    def capvals_from_res(self, res):
        for r in res:
            if r[0] == "clo":
                self.capvals.setdefault(r[1].key, list(r[1].caps))

    def maywritten(self, g):
        mw = {k: set() for k in g.nodes}
        changed = True
        while changed:
            changed = False
            for k, nd in g.nodes.items():
                out = mw[k] | set(nd.writes)
                for t in succs_of(nd):
                    if not out <= mw[t]:
                        mw[t] |= out
                        changed = True
        return mw

    def fold_decrypt(self, g, env0, mw=None):
        ev = self.ev
        for nd in g.nodes.values():
            env = dict(env0)
            if mw is not None:
                env["regs"] = {n: v for n, v in env0["regs"].items() if n not in mw[nd.key]}
            rep = {}
            env["tmps"] = {}

            def f(e):
                if e[0] == "tmp" and e[1] in rep:
                    return rep[e[1]]
                return None

            stmts = []
            for s in nd.stmts:
                s = mapstmt(s, f)
                if s[0] == "let":
                    tid, e = s[1], s[2]
                    if e[0] == "cellval":
                        v = self.known(e, env)
                        if v is not None:
                            env["tmps"][tid] = v
                    elif e[0] == "closure":
                        vals = [self.known(c, env) for c in e[2]]
                        if e[1] not in self.capvals:
                            self.capvals[e[1]] = vals
                    elif e[0] == "call" and ev.decoder is not None:
                        fv = self.known(e[1], env)
                        a = e[2]
                        if self.classify(fv) == "dec" and len(a) == 2 and is_const(a[0]) and is_const(a[1]) and type(a[0][1]) is str and type(a[1][1]) in (int, float):
                            try:
                                pt = ev.decode(a[0][1], a[1][1])
                            except (Stuck, LuaErr, RecursionError):
                                pt = None
                            if type(pt) is str:
                                self.plain[a[1][1]] = pt
                                rep[tid] = ("const", a[1][1])
                                continue
                    elif e[0] == "index":
                        ov = self.known(e[1], env)
                        if self.classify(ov) == "proxy" and is_const(e[2]) and e[2][1] in self.plain:
                            rep[tid] = ("const", self.plain[e[2][1]])
                            continue
                stmts.append(s)
            nd.stmts = stmts
            if rep:
                nd.writes = {n: mapx(v, f) for n, v in nd.writes.items()}
                if nd.term[0] == "branch":
                    nd.term = ("branch", mapx(nd.term[1], f), nd.term[2], nd.term[3])
                if nd.ret is not None:
                    nd.ret = mapx(nd.ret, f)

    def graph(self, key, main):
        g = Graph(self.an, key)
        if not main:
            self.fold_decrypt(g, {"regs": {}, "caps": self.capvals.get(key), "tmps": {}})
        if main:
            dec = self.ev.decoder
            if dec is not None:
                for tid, clo in self.ev.callee_obs.items():
                    if clo.key == dec.key:
                        g.pure_tids.add(tid)
            apply_obs(g, self.ev, self.complete)
        g.simplify()
        g.alias_cells()
        return g

    def closures_of(self, g):
        keys = []

        def f(e):
            if e[0] == "closure":
                self.arity[e[1]] = e[3]
                if e[1] not in keys:
                    keys.append(e[1])

        for nd in g.nodes.values():
            for e in node_exprs(nd):
                walk(e, f)
        return keys

    def all_regs(self, g):
        regs = set()
        for nd in g.nodes.values():
            regs |= regs_used(node_exprs(nd))
            regs |= set(nd.writes)
        return sorted(regs)

    def body_lines(self, g, ind, init=None):
        pr = self.pr
        pr.prepare(g)
        for nd in g.nodes.values():
            fold_block(nd, pr.multi, g.hoist)
        pr.prepare(g)
        pad = "    " * ind
        out = []
        for tid in g.hoist:
            out.append("%slocal %s = {}" % (pad, pr.tn(tid)))
        regs = self.all_regs(g)
        if regs:
            if init:
                out.append(pad + "local " + ", ".join("r_" + r for r in regs) + " = " + ", ".join(init.get(r, "nil") for r in regs))
            else:
                out.append(pad + "local " + ", ".join("r_" + r for r in regs))
        for reg, expr in self.an.prologue:
            if reg in regs:
                out.append("%sr_%s = %s" % (pad, reg, pr.x(pr.conv_prologue(expr))))
        try:
            tree = polish(Struct(g, pr).region(g.key, None, None))
            tree = recover_for(tree)
            render_tree(tree, ind, out)
        except Unstructured as ex:
            self.fallbacks.append((g.key, str(ex)))
            out.extend(self.machine(g, ind))
        return out

    def machine(self, g, ind):
        pr = self.pr
        pad = "    " * ind
        out = [pad + "local pc = %d" % g.key, pad + "while pc do"]
        first = True
        for k, nd in g.nodes.items():
            out.append("%s    %s pc == %d then" % (pad, "if" if first else "elseif", k))
            first = False
            ipad = pad + "        "
            for line in pr.stmt_lines(nd):
                out.append(ipad + line)
            t = nd.term
            if t[0] == "ret":
                rt = pr.ret_text(nd)
                out.append(ipad + ("return " + rt if rt else "return"))
                continue
            if t[0] == "branch":
                out.append("%slocal c = %s" % (ipad, pr.x(t[1])))
            for line in pr.write_lines(nd):
                out.append(ipad + line)
            if t[0] == "goto":
                out.append("%spc = %d" % (ipad, t[1]))
            else:
                a = "pc = %d" % t[2] if t[2] is not None else "pc = nil"
                b = "pc = %d" % t[3] if t[3] is not None else "pc = nil"
                out.append("%sif c then %s else %s end" % (ipad, a, b))
        out.append(pad + "    else")
        out.append(pad + "        error(\"invalid state\")")
        out.append(pad + "    end")
        out.append(pad + "end")
        return out

    def assemble(self, funcs, order, prefix_lines, main_g, init):
        namer = Namer()
        bodies = {}
        for k in order:
            text = "\n".join(self.body_lines(funcs[k], 0))
            bodies[k] = rename(text, namer)
        pre = rename("\n".join(prefix_lines), namer) if prefix_lines else ""
        main = ""
        if main_g is not None:
            main = rename("\n".join(self.body_lines(main_g, 0, init)), namer)
        inl = Inliner(bodies, self.arity)
        main = inl.expand(main)
        text = (pre + "\n" if pre else "") + main
        text = promote_cells(text)
        pending = []
        seen = set()

        def refs(t):
            m, _ = mask(t)
            return [int(x.group(1)) for x in MAKE.finditer(m)] + [int(x) for x in __import__("re").findall(r"make\[(\d+)\]", m)]

        queue = list(dict.fromkeys(refs(text)))
        defs = []
        while queue:
            k = queue.pop(0)
            if k in seen or k not in bodies:
                continue
            seen.add(k)
            body = inl.expand(bodies[k], (k,))
            body = promote_cells(body)
            defs.append((k, body))
            queue.extend(refs(body))
        out = []
        if defs:
            out.append("local make = {}")
            out.append("")
            for k, body in defs:
                out.append("make[%d] = function(up)" % k)
                out.append("    return function(...)")
                out.append("\n".join(("        " + ln if ln.strip() else ln) for ln in body.split("\n")))
                out.append("    end")
                out.append("end")
                out.append("")
        out.append(text)
        return "\n".join(out).rstrip("\n") + "\n"

    def collect(self, g):
        funcs = {}
        order = []
        queue = self.closures_of(g)
        while queue:
            k = queue.pop(0)
            if k in funcs:
                continue
            fg = self.graph(k, False)
            funcs[k] = fg
            order.append(k)
            queue.extend(self.closures_of(fg))
        return funcs, order

    def render(self):
        main = self.graph(self.an.entry[0], True)
        funcs, order = self.collect(main)
        return self.assemble(funcs, order, [], main, None)

    def graph_resume(self, state, regs):
        g = Graph(self.an, state)
        mw = self.maywritten(g)
        consts = {}
        for n, v in regs.items():
            o = okey(v)
            if o is not TOP:
                consts[n] = ("const", o[1])
        for nd in g.nodes.values():
            def f(e, nd=nd):
                if e[0] == "reg" and len(e) == 2 and e[1] in consts and e[1] not in mw[nd.key]:
                    return consts[e[1]]
                return None

            nd.stmts = [mapstmt(s, f) for s in nd.stmts]
            nd.writes = {n: mapx(v, f) for n, v in nd.writes.items()}
            if nd.term[0] == "branch":
                nd.term = ("branch", mapx(nd.term[1], f), nd.term[2], nd.term[3])
            if nd.ret is not None:
                nd.ret = mapx(nd.ret, f)
        self.fold_decrypt(g, {"regs": dict(regs), "caps": None, "tmps": {}}, mw)
        g.simplify()
        g.alias_cells()
        return g

    def render_resume(self, prefix_lines, g, init, extra_keys):
        funcs = {}
        order = []
        queue = list(extra_keys)
        if g is not None:
            queue.extend(self.closures_of(g))
        while queue:
            kk = queue.pop(0)
            if kk in funcs:
                continue
            fg = self.graph(kk, False)
            funcs[kk] = fg
            order.append(kk)
            queue.extend(self.closures_of(fg))
        return self.assemble(funcs, order, prefix_lines, g, init)
