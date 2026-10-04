import math
from .parser import INF


def truthy(v):
    return v is not None and v is not False


def num_norm(x):
    if isinstance(x, float) and x == x and x not in (INF, -INF) and x == int(x) and abs(x) < 2 ** 53:
        return int(x)
    return x


def fold_bin(op, a, b):
    if op in ("+", "-", "*", "/", "%", "^"):
        if type(a) in (int, float) and type(b) in (int, float) and type(a) is not bool and type(b) is not bool:
            a = float(a)
            b = float(b)
            try:
                if op == "+":
                    r = a + b
                elif op == "-":
                    r = a - b
                elif op == "*":
                    r = a * b
                elif op == "/":
                    if b == 0:
                        return NotImplemented
                    r = a / b
                elif op == "%":
                    if b == 0:
                        return NotImplemented
                    r = a - math.floor(a / b) * b
                else:
                    r = a ** b
                    if isinstance(r, complex):
                        return NotImplemented
            except (OverflowError, ZeroDivisionError):
                return NotImplemented
            return num_norm(r)
        return NotImplemented
    if op == "..":
        if type(a) in (str, int, float) and type(b) in (str, int, float) and type(a) is not bool and type(b) is not bool:
            return lua_str(a) + lua_str(b)
        return NotImplemented
    if op in ("==", "~="):
        if type(a) is bool or type(b) is bool or a is None or b is None:
            eq = a is b
        elif type(a) in (int, float) and type(b) in (int, float):
            eq = a == b
        elif type(a) is str and type(b) is str:
            eq = a == b
        else:
            return NotImplemented
        return eq if op == "==" else not eq
    if op in ("<", ">", "<=", ">="):
        if type(a) in (int, float) and type(b) in (int, float) and type(a) is not bool and type(b) is not bool or (type(a) is str and type(b) is str):
            if op == "<":
                return a < b
            if op == ">":
                return a > b
            if op == "<=":
                return a <= b
            return a >= b
    return NotImplemented


def lua_str(v):
    if type(v) is str:
        return v
    if type(v) is float:
        return "%.14g" % v
    return str(v)


def is_const(e):
    return e[0] == "const"


def mk_bin(op, a, b):
    if is_const(a) and is_const(b):
        r = fold_bin(op, a[1], b[1])
        if r is not NotImplemented:
            return ("const", r)
    return ("bin", op, a, b)


def mk_un(op, a):
    if is_const(a):
        v = a[1]
        if op == "not":
            return ("const", not truthy(v))
        if op == "-" and type(v) in (int, float) and type(v) is not bool:
            return ("const", num_norm(-v))
        if op == "#" and type(v) is str:
            return ("const", len(v))
    return ("un", op, a)


def mk_and(a, b):
    if is_const(a):
        return b if truthy(a[1]) else a
    return ("and", a, b)


def mk_or(a, b):
    if is_const(a):
        return a if truthy(a[1]) else b
    return ("or", a, b)
