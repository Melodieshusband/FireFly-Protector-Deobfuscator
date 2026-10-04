INF = float("inf")


def norm(x):
    if type(x) is float and x == x and x not in (INF, -INF) and x == int(x) and abs(x) < 2 ** 63:
        return int(x)
    return x


PREC = {"or": 1, "and": 2, "<": 3, ">": 3, "<=": 3, ">=": 3, "~=": 3, "==": 3, "..": 4, "+": 5, "-": 5, "*": 6, "/": 6, "%": 6, "^": 8}


RIGHT = {"..", "^"}


class LuaParser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def nxt(self):
        t = self.t[self.i]
        self.i += 1
        return t

    def is_(self, v):
        t = self.peek()
        return t is not None and t.val == v and t.kind in ("op", "kw")

    def accept(self, v):
        if self.is_(v):
            self.i += 1
            return True
        return False

    def expect(self, v):
        if not self.accept(v):
            raise SyntaxError("expected %r got %r at token %d" % (v, self.peek(), self.i))

    def block(self, enders=()):
        out = []
        while self.peek() is not None:
            t = self.peek()
            if t.kind == "kw" and t.val in enders:
                break
            if self.accept(";"):
                continue
            if self.is_("return"):
                self.i += 1
                exprs = []
                t2 = self.peek()
                if t2 is not None and not (t2.kind == "kw" and t2.val in ("end", "else", "elseif", "until")) and not self.is_(";"):
                    exprs = self.exprlist()
                self.accept(";")
                out.append(("return", exprs))
                break
            out.append(self.stmt())
        return out

    def stmt(self):
        t = self.peek()
        if t.kind == "kw":
            v = t.val
            if v == "local":
                self.i += 1
                if self.accept("function"):
                    name = self.nxt().val
                    return ("localfunc", name, self.funcbody())
                names = [self.nxt().val]
                while self.accept(","):
                    names.append(self.nxt().val)
                exprs = self.exprlist() if self.accept("=") else []
                return ("local", names, exprs)
            if v == "function":
                self.i += 1
                target = ("name", self.nxt().val)
                is_method = False
                while self.is_(".") or self.is_(":"):
                    sep = self.nxt().val
                    key = ("const", self.nxt().val)
                    target = ("index", target, key)
                    if sep == ":":
                        is_method = True
                        break
                f = self.funcbody(is_method)
                return ("assign", [target], [f])
            if v == "if":
                self.i += 1
                clauses = []
                cond = self.expr(0)
                self.expect("then")
                clauses.append((cond, self.block(("elseif", "else", "end"))))
                other = None
                while True:
                    if self.accept("elseif"):
                        c = self.expr(0)
                        self.expect("then")
                        clauses.append((c, self.block(("elseif", "else", "end"))))
                    elif self.accept("else"):
                        other = self.block(("end",))
                    else:
                        break
                self.expect("end")
                return ("if", clauses, other)
            if v == "while":
                self.i += 1
                cond = self.expr(0)
                self.expect("do")
                body = self.block(("end",))
                self.expect("end")
                return ("while", cond, body)
            if v == "repeat":
                self.i += 1
                body = self.block(("until",))
                self.expect("until")
                return ("repeat", body, self.expr(0))
            if v == "for":
                self.i += 1
                n1 = self.nxt().val
                if self.accept("="):
                    a = self.expr(0)
                    self.expect(",")
                    b = self.expr(0)
                    c = self.expr(0) if self.accept(",") else None
                    self.expect("do")
                    body = self.block(("end",))
                    self.expect("end")
                    return ("fornum", n1, a, b, c, body)
                names = [n1]
                while self.accept(","):
                    names.append(self.nxt().val)
                self.expect("in")
                exprs = self.exprlist()
                self.expect("do")
                body = self.block(("end",))
                self.expect("end")
                return ("forin", names, exprs, body)
            if v == "do":
                self.i += 1
                body = self.block(("end",))
                self.expect("end")
                return ("do", body)
            if v == "break":
                self.i += 1
                return ("break",)
        e = self.suffixed()
        if self.is_("=") or self.is_(","):
            targets = [e]
            while self.accept(","):
                targets.append(self.suffixed())
            self.expect("=")
            return ("assign", targets, self.exprlist())
        return ("call", e)

    def funcbody(self, method=False):
        self.expect("(")
        params = ["self"] if method else []
        vararg = False
        while not self.accept(")"):
            if self.is_("..."):
                self.i += 1
                vararg = True
            else:
                params.append(self.nxt().val)
            self.accept(",")
        body = self.block(("end",))
        self.expect("end")
        return ("func", params, vararg, body)

    def exprlist(self):
        out = [self.expr(0)]
        while self.accept(","):
            out.append(self.expr(0))
        return out

    def expr(self, minp):
        t = self.peek()
        if (t.kind == "kw" and t.val == "not") or (t.kind == "op" and t.val in ("-", "#")):
            self.i += 1
            left = ("un", t.val, self.expr(7))
        else:
            left = self.simple()
        while True:
            t = self.peek()
            if t is None:
                break
            op = t.val if t.kind in ("op", "kw") else None
            if op not in PREC or PREC[op] < minp:
                break
            p = PREC[op]
            self.i += 1
            right = self.expr(p if op in RIGHT else p + 1)
            if op == "and":
                left = ("and", left, right)
            elif op == "or":
                left = ("or", left, right)
            else:
                left = ("bin", op, left, right)
        return left

    def simple(self):
        t = self.peek()
        if t.kind == "num":
            self.i += 1
            v = t.val
            if v.lower().startswith("0x"):
                return ("const", int(v, 16))
            return ("const", norm(float(v)))
        if t.kind == "str":
            self.i += 1
            return ("const", t.val)
        if t.kind == "kw" and t.val in ("nil", "true", "false"):
            self.i += 1
            return ("const", {"nil": None, "true": True, "false": False}[t.val])
        if t.kind == "op" and t.val == "...":
            self.i += 1
            return ("vararg",)
        if t.kind == "op" and t.val == "{":
            return self.table()
        if t.kind == "kw" and t.val == "function":
            self.i += 1
            return self.funcbody()
        return self.suffixed()

    def table(self):
        self.expect("{")
        items = []
        while not self.accept("}"):
            t = self.peek()
            if t.kind == "op" and t.val == "[":
                self.i += 1
                k = self.expr(0)
                self.expect("]")
                self.expect("=")
                items.append(("kv", k, self.expr(0)))
            elif t.kind == "name" and self.peek(1) is not None and self.peek(1).val == "=" and self.peek(1).kind == "op":
                self.i += 2
                items.append(("kv", ("const", t.val), self.expr(0)))
            else:
                items.append(("pos", self.expr(0)))
            if not (self.accept(",") or self.accept(";")):
                self.expect("}")
                break
        return ("table", items)

    def suffixed(self):
        t = self.nxt()
        if t.kind == "name":
            e = ("name", t.val)
        elif t.kind == "op" and t.val == "(":
            e = ("paren", self.expr(0))
            self.expect(")")
        else:
            raise SyntaxError("bad primary %r at %d" % (t, self.i))
        while True:
            t = self.peek()
            if t is None:
                break
            if t.kind == "op" and t.val == ".":
                self.i += 1
                e = ("index", e, ("const", self.nxt().val))
            elif t.kind == "op" and t.val == "[":
                self.i += 1
                k = self.expr(0)
                self.expect("]")
                e = ("index", e, k)
            elif t.kind == "op" and t.val == ":":
                self.i += 1
                name = self.nxt().val
                args = self.callargs()
                e = ("method", e, name, args)
            elif (t.kind == "op" and t.val == "(") or t.kind == "str" or (t.kind == "op" and t.val == "{"):
                e = ("callx", e, self.callargs())
            else:
                break
        return e

    def callargs(self):
        t = self.peek()
        if t.kind == "str":
            self.i += 1
            return [("const", t.val)]
        if t.kind == "op" and t.val == "{":
            return [self.table()]
        self.expect("(")
        if self.accept(")"):
            return []
        args = self.exprlist()
        self.expect(")")
        return args
