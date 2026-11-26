import argparse
import re
import sys
from .analysis import Analysis
from .emitter import Emitter, collect_strings, q
from .decompile import Decompiler, Observer
from .evaluator import DEFAULT_ASSUMED, Evaluator
from .render import render_residual, rv
from .values import Bi, Clo, Obj, Sym

BANNER_VERSION = re.compile(r"\bv\.\s*(\d+)\.(\d+)\.(\d+)")


def detect_version(src):
    m = None
    for m in BANNER_VERSION.finditer(src[-4000:]):
        pass
    if m is None:
        return None
    return tuple(int(x) for x in m.groups())


def run_static_v2(args, src):
    from .v2 import V2Error, Analysis2, decoded_strings, render_blocks
    from .v2host import static_run
    from .emulate import quote

    try:
        an = Analysis2(src)
    except V2Error as ex:
        sys.stderr.write("error: unsupported protector v2 container (%s)\n" % (ex,))
        return 1
    if args.raw:
        text = render_blocks(an)
        res = None
    else:
        try:
            res = static_run(src, assume=args.assume, strict=args.strict_env, step_limit=args.step_limit)
        except V2Error as ex:
            sys.stderr.write("error: unsupported protector v2 layout (%s)\n" % (ex,))
            return 1
        text = res.text
    if args.output:
        with open(args.output, "w", encoding="latin-1") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    if args.report:
        sys.stderr.write("protector version: %s\n" % ".".join(str(x) for x in an.version))
        sys.stderr.write("state records: %d, blocks: %d, entry state: %s\n" % (len(an.records), an.block_count(), an.entry_state()))
        sys.stderr.write("container integrity: checksums, CRC and record stream verified\n")
        if res is not None:
            if res.limit_hit:
                status = "stopped: step limit reached"
            elif res.error is not None:
                status = "stopped by a VM error: %s" % (res.error,)
            else:
                status = "VM program evaluated to completion"
            sys.stderr.write("status: %s\n" % status)
            sys.stderr.write("recorded statements: %d\n" % len(res.lines))
    if args.strings:
        for bi, mode, val in decoded_strings(an):
            if isinstance(val, str):
                sys.stderr.write("block %d mode %d %s\n" % (bi, mode, quote(val)))
            else:
                sys.stderr.write("block %d mode %d %r\n" % (bi, mode, val))
    if res is not None and (res.limit_hit or res.error is not None):
        return 2
    return 0


def run_emulation(args, src):
    from .emulate import emulate

    try:
        res = emulate(src, assume=args.assume, strict=args.strict_env, step_limit=args.step_limit)
    except SyntaxError as ex:
        sys.stderr.write("error: the script could not be parsed (%s)\n" % (ex,))
        return 1
    if args.output:
        with open(args.output, "w", encoding="latin-1") as fh:
            fh.write(res.text)
    else:
        sys.stdout.write(res.text)
    if args.report:
        version = detect_version(src)
        if version is not None:
            sys.stderr.write("protector version: %d.%d.%d\n" % version)
        if res.limit_hit:
            status = "emulation stopped: step limit reached"
        elif res.error is not None:
            status = "emulation stopped by a script error: %s" % (res.error,)
        else:
            status = "emulated to completion"
        sys.stderr.write("status: %s\n" % status)
        sys.stderr.write("recorded statements: %d\n" % len(res.lines))
        sys.stderr.write("loadstring layers: %d\n" % len(res.loaded))
    if res.limit_hit or res.error is not None:
        return 2
    return 0


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
    ap = argparse.ArgumentParser(
        description="Firefly deobfuscator: static by default, --emulate runs the script in a sandboxed interpreter"
    )
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--strings", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--ast", action="store_true")
    ap.add_argument("--ast-all", action="store_true")
    ap.add_argument("--assume", action="append", default=[], metavar="NAME")
    ap.add_argument("--strict-env", action="store_true")
    ap.add_argument("--emulate", action="store_true")
    ap.add_argument("--step-limit", type=int, default=50000000)
    args = ap.parse_args(argv)
    with open(args.input, "r", encoding="latin-1") as fh:
        src = fh.read()
    if args.emulate:
        return run_emulation(args, src)
    version = detect_version(src)
    if version is not None and version[0] >= 2:
        return run_static_v2(args, src)
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
