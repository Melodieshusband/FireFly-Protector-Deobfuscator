<div align="center">

# Firefly-deobf

**Static deobfuscator for Lua and Luau scripts protected with Firefly.**

Made by **Melodieshusband (Meloten)**

![python](https://img.shields.io/badge/python-3.8%2B-blue)
![dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![license](https://img.shields.io/badge/license-Apache%202.0-blue)
![status](https://img.shields.io/badge/status-experimental-orange)
![execution](https://img.shields.io/badge/runs%20the%20script-never-success)

[Status](#project-status) · [Quick start](#quick-start) · [Modes](#modes) · [Examples](#examples) · [Limitations](#limitations) · [Tests](#tests) · [Layout](#layout)

</div>

---

## What it does

The protected script is **never executed**. The tool parses the protected
chunk, decodes its state table and both string cipher layers, and then either
evaluates the virtual machine symbolically or lifts it back into structured
Lua.

| | |
| --- | --- |
| Decodes | state table, container, operator tables, two string cipher layers |
| Evaluates | symbolic evaluation of the VM until the payload falls out |
| Decompiles | `if` / `while` / numeric `for` / generic `for`, inline closures |
| Dependencies | none, only the Python standard library |

## Project status

> [!WARNING]
> **Read this first.** This project was developed and verified against only
> four protected samples, all in [`samples/`](samples). The Firefly website
> rejected the stress test suite written for this tool: every upload ended
> with *"Protection failed – processing failed"*. The suite in
> [`tests/stress/`](tests/stress) has therefore never been run through the
> obfuscator.
>
> I (Melodieshusband) did everything I could with the material I had. Bugs on inputs outside the
> four samples are expected, and anyone who can produce protected files is
> welcome to continue from here.

What that means in practice:

| Area | State |
| --- | --- |
| Four bundled samples | verified, covered by unit tests |
| Stress suite (`tests/stress/`) | written, **never obfuscated**, expected values computed by hand |
| Generated Lua | checked by reading and by the bundled lexer, **not run** in a real Lua or Luau runtime |
| Runtime comparison test | present, skipped automatically when no Lua binary is found |
| Everything else | untested |

Run the plain stress source first and make sure it prints `failed 0` before
trusting it.

## Quick start

Requires Python 3.8 or newer.

```bash
git clone https://github.com/Melodieshusband/firefly-deobf.git
cd firefly-deobf
python -m firefly_deobf samples/static_protected.lua
```

```bash
python -m firefly_deobf input.lua -o output.lua --report
python -m firefly_deobf input.lua --ast
python -m firefly_deobf input.lua --ast-all
python -m firefly_deobf input.lua --strings
python -m firefly_deobf input.lua --assume getgenv --assume loadstring
```

| Flag | Meaning |
| --- | --- |
| `-o`, `--output` | write the result to a file instead of stdout |
| `--report` | print the evaluation status and warnings to stderr |
| `--strings` | dump decoded string constants to stderr |
| `--raw` | skip evaluation and emit the raw lifted program as a state machine |
| `--ast` | default evaluation, but the part that could not be evaluated is emitted as structured code |
| `--ast-all` | skip evaluation and decompile the whole program into structured code |
| `--assume NAME` | treat the global `NAME` as defined and truthy (repeatable) |
| `--strict-env` | assume no globals except the ones given with `--assume` |

## Modes

| Mode | Output |
| --- | --- |
| default, fully evaluated | the payload as a trace of its calls, with every decoded constant and folded expression |
| default, partial | the evaluated prefix as plain Lua, then the rest as a state machine that resumes where evaluation stopped |
| `--ast` | the same prefix, then the rest as structured code |
| `--ast-all` | the whole program as structured code, with decoded strings |
| `--raw` | the whole program as a state machine |

Evaluation stops when a branch depends on a value that only exists at runtime,
for example `os.time()` or an unknown global. Everything before that point is
still resolved. `--report` prints which kind of result you got.

## Examples

### Fully evaluated

The smallest sample is a 57 KB protected file. The result is:

```lua
print("Hello from a deobfuscated script lol")
```

### Structured output

Original source:

```lua
local fns = {}
for i = 1, 3 do
    fns[i] = function()
        i = i + 10
        return i
    end
end
print(fns[1](), fns[1](), fns[2](), fns[3]())
```

Recovered with `--ast-all` (names are generated, structure and values are
real):

```lua
for r28 = 1, 3 do
    local l94
    l94 = r28
    local l95 = l94
    local l96 = function()
        local l20 = l94
        l94 = (l20 + 10)
        return l94
    end
    r33[l95] = l96
end
r33[1]()
r33[1]()
r33[2]()
print(11, 21, 12, r33[3]())
```

<details>
<summary>Partial result with <code>--ast</code></summary>

When a value only exists at runtime, evaluation stops and the rest of the
program is emitted as structured code:

```lua
local v3 = os.time()
local v4 = (v3 % 7)
local v5 = (1 * v4)
local v6 = (v5 % 5)
...
if (r6 > 3) then
    print("high")
else
    print("low")
end
```

</details>

## What the structured output recovers

- `if` / `elseif` / `else`, `while`, `break`, numeric `for`, and generic
  `for ... in pairs / ipairs / gmatch(...)` loops
- decoded string constants from both cipher layers
- closures as inline `function(...) ... end` when their captured variables can
  be resolved, otherwise as `make[key]({...})`
- captured variables as plain locals when possible, otherwise as tables with a
  `.v` field

## Limitations

- Original names of locals, functions and fields are gone. Everything is named
  `r1`, `l1`, `k1` and so on.
- Method calls come out as `obj["method"](obj)` instead of `obj:method()`.
- Some loops stay as `while true` with an explicit `break`.
- No comments, types or original formatting.
- `--ast` and the default mode fold values observed while evaluating the main
  chunk. Functions called with different arguments are not folded.
- Coroutines, unusual `pcall` / `error` flows and metatable-heavy code are
  expected to stop evaluation early. None of this has been tested against real
  protected output.

## Environment model

Globals that exist in Roblox and standard Lua are assumed to be defined, so
guards such as `wait or function() end` resolve to `wait`.

<details>
<summary>Default assumed globals</summary>

`wait spawn delay tick time warn game workspace script shared task typeof
Instance Vector3 Vector2 CFrame Color3 UDim UDim2 Enum os coroutine debug utf8
bit32`

Use `--assume` to add names and `--strict-env` to start from an empty list.

</details>

Constants and branches resolved under these assumptions are folded into the
output as if they were always true. Number formatting and string handling
follow Luau and Lua 5.1 semantics, where integers and floats are not
distinguished. The main chunk is assumed to be called without arguments.

## Tests

```bash
python -m unittest discover -s tests -t .
```

The unit tests cover the four samples. [`tests/stress/`](tests/stress) holds
the Luau stress suite:

| File | Content |
| --- | --- |
| `stress_test.lua` | one file, 108 named checks, prints `passed N failed M` |
| `split/a_*` – `split/h_*` | 8 small files, plain Lua constructs |
| `split/l1_*` – `split/l10_*` | 10 small files, Luau-only syntax and libraries |

If you can protect these files with Firefly, run the protected output through
this tool and compare it with the source. Every check has a name, so a failure
points at the exact feature.

## Layout

```
firefly_deobf/
  lexer.py        tokenizer
  parser.py       expression and block parser
  program.py      structure of the protected chunk
  container.py    state table container decoding
  analysis.py     state table, operator tables, entry point
  lifter.py       per-state lifting of the virtual machine blocks
  folding.py      constant folding
  values.py       runtime values for the evaluator
  evaluator.py    symbolic evaluator
  ciphers.py      string cipher handling
  decompile.py    structured decompiler (--ast, --ast-all)
  refine.py       renaming, closure inlining, cell promotion
  emitter.py      state machine output
  render.py       output of the evaluated prefix
  cli.py          command line interface
samples/          four protected samples and their sources
tests/            unit tests and the stress suite
```

## Responsible use

This tool is meant for analysing scripts that you own or are authorised to
analyse.

## License

Copyright 2026 Melodieshusband.

Licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE).
