import argparse
import sys
from .analysis import Analysis
from .emitter import Emitter, collect_strings, q
from .decompile import Decompiler, Observer
from .evaluator import DEFAULT_ASSUMED, Evaluator
from .render import render_residual, rv
from .values import Bi, Clo, Obj, Sym


class Unsupported(Exception):
    pass


def walk_values(x, seen=None):
    if isinstance(x, (tuple, list)):
        for y in x:
            yield from walk_values(y)
    elif type(x) in (Bi, Obj, Clo, Sym):
        yield x


def check_renderable(ev, res, regs):
    for entry in res:
        for v in walk_values(entry):
            if type(v) is Bi and v.name not in ev.builtins and not v.name.startswith("string.") and not v.name.startswith("table.") and not v.name.startswith("math."):
                raise Unsupported("builtin %s has no source form" % v.name)
    for v in regs.values():
        if type(v) is Bi and v.name not in ev.builtins:
            raise Unsupported("builtin %s has no source form" % v.name)


def build_partial(an, ev, result):
    kind, state, fr = result
    resume = None
    if kind == "stuck":
        for reg, v in sorted(fr.regs.items()):
            ev.escape(v)
        check_renderable(ev, ev.res, fr.regs)
        resume = {name: v for name, v in fr.regs.items()}
    else:
        check_renderable(ev, ev.res, {})
    prefix = render_residual(ev.res, list(resume.values()) if resume is not None else ()).rstrip("\n")
    tail = prefix.split("\n") if prefix else []
    if resume is not None:
        items = ", ".join("[%s] = %s" % (q(n), rv(v)) for n, v in sorted(resume.items()))
        tail.append("RESUME = {pc = %d, regs = {%s}}" % (state, items))
        tail.append("return make[%d]({})(...)" % an.entry[0])
    return Emitter(an).render(tail=tail, resume=resume)


def build_ast(an, ev, result):
    kind, state, fr = result
    dec = Decompiler(an, ev, False)
    dec.capvals_from_res(ev.res)
    g = None
    init = {}
    resume = {}
    if kind == "stuck":
        g = dec.graph_resume(state, fr.regs)
        for reg in sorted(g.live_entry()):
            v = fr.regs.get(reg)
            resume[reg] = v
            ev.escape(v)
        check_renderable(ev, ev.res, resume)
    else:
        check_renderable(ev, ev.res, {})
    prefix = render_residual(ev.res, list(resume.values())).rstrip("\n")
    for reg, v in resume.items():
        init[reg] = rv(v)
    tail = prefix.split("\n") if prefix else []
    return dec.render_resume(tail, g, init, list(dict.fromkeys(ev.need_keys)))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Firefly static deobfuscator (no script execution)")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--strings", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--ast", action="store_true")
    ap.add_argument("--ast-all", action="store_true")
    ap.add_argument("--assume", action="append", default=[], metavar="NAME")
    ap.add_argument("--strict-env", action="store_true")
    args = ap.parse_args(argv)
    with open(args.input, "r", encoding="latin-1") as fh:
        src = fh.read()
    try:
        an = Analysis(src)
    except (ValueError, KeyError, IndexError, AttributeError, SyntaxError, TypeError) as ex:
        sys.stderr.write("error: input is not a supported Firefly legacy script (%s)\n" % (ex,))
        return 1
    text = None
    status = "raw lift"
    if args.ast_all:
        assumed = set(args.assume) if args.strict_env else set(DEFAULT_ASSUMED) | set(args.assume)
        ev = Observer(an, assume=assumed)
        r = ev.run_main(an.entry[0])
        text = Decompiler(an, ev, r[0] == "ret").render()
        status = "structured lift of the whole program"
    elif not args.raw:
        assumed = set(args.assume) if args.strict_env else set(DEFAULT_ASSUMED) | set(args.assume)
        ev = Evaluator(an, assume=assumed)
        r = ev.run_main(an.entry[0])
        if r[0] == "ret" and not ev.need_keys:
            text = render_residual(ev.res)
            status = "fully evaluated, %d steps" % ev.steps
        else:
            reason = ev.stuck_reason if r[0] == "stuck" else "closures escape the evaluated part"
            try:
                if args.ast:
                    text = build_ast(an, ev, r)
                    status = "partial: evaluated prefix plus structured remainder (%s)" % reason
                else:
                    text = build_partial(an, ev, r)
                    status = "partial: evaluated prefix plus remaining state machine (%s)" % reason
            except Unsupported as ex:
                status = "partial evaluation stopped: %s; raw lift (%s)" % (reason, ex)
    if text is None:
        text = Emitter(an).render()
    if args.output:
        with open(args.output, "w", encoding="latin-1") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    if args.report:
        sys.stderr.write("state records: %d\n" % len(an.T))
        sys.stderr.write("status: %s\n" % status)
        for w in an.warnings:
            sys.stderr.write("warning: %s\n" % w)
    if args.strings:
        for mode, val in collect_strings(an):
            if isinstance(val, str):
                sys.stderr.write("%d %s\n" % (mode, q(val)))
    return 0
