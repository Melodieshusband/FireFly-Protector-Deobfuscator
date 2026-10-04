local passed, failed = 0, 0

local function check(name: string, got: any, expected: any)
    if got == expected then
        passed += 1
    else
        failed += 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

type Point = {x: number, y: number}
type Pair<T> = {first: T, second: T}

local fns = {}
for i = 1, 3 do
    fns[i] = function()
        i += 10
        return i
    end
end
check("loop_closure_1", fns[1](), 11)
check("loop_closure_1b", fns[1](), 21)
check("loop_closure_2", fns[2](), 12)
check("loop_closure_3", fns[3](), 13)

local makers = {}
for i = 1, 2 do
    local base = i * 100
    for j = 1, 2 do
        makers[#makers + 1] = function()
            base += j
            return base
        end
    end
end
check("nested_closures", makers[1]() .. "," .. makers[2]() .. "," .. makers[3]() .. "," .. makers[4](), "101,103,201,203")

local function counter()
    local n = 0
    return function()
        n += 1
        return n
    end, function()
        return n
    end
end
local inc, get = counter()
inc()
inc()
inc()
check("shared_upvalue", get(), 3)

local function adder(a: number)
    return function(b: number)
        return function(c: number)
            return a + b + c
        end
    end
end
check("curry", adder(1)(2)(3), 6)

local function fact(n: number): number
    if n <= 1 then
        return 1
    end
    return n * fact(n - 1)
end
check("recursion", fact(10), 3628800)

local isodd
local function iseven(n: number): boolean
    if n == 0 then
        return true
    end
    return isodd(n - 1)
end
function isodd(n: number): boolean
    if n == 0 then
        return false
    end
    return iseven(n - 1)
end
check("mutual_even", iseven(10), true)
check("mutual_odd", isodd(7), true)

local function mix(a: number, b: number): number
    return a * 10 + b
end
check("args_a", mix(1, 2), 12)
check("args_b", mix(3, 4), 34)
check("args_c", mix(5, 6), 56)

local function sgn(x: number): number
    return if x > 0 then 1 elseif x < 0 then -1 else 0
end
check("if_expression", sgn(9) * 100 + sgn(-4) * 10 + sgn(0), 90)

local function classify(n: number): string
    if n < 0 then
        return "neg"
    elseif n == 0 then
        return "zero"
    elseif n < 10 then
        return "small"
    elseif n < 100 then
        return "medium"
    else
        return "large"
    end
end
local classes = {}
for _, n in {-5, 0, 7, 50, 500} do
    classes[#classes + 1] = classify(n)
end
check("elseif_chain", table.concat(classes, ","), "neg,zero,small,medium,large")

local function find_pair(target: number)
    for i = 1, 9 do
        for j = i, 9 do
            if i * j == target then
                return i, j
            end
        end
    end
    return nil
end
local p1, p2 = find_pair(42)
check("early_return", p1 * 100 + p2, 607)

local function va(...)
    local t = {...}
    return select("#", ...), #t, (select(2, ...))
end
local vc, vl, vs = va(10, 20, 30)
check("vararg_count", vc, 3)
check("vararg_length", vl, 3)
check("vararg_second", vs, 20)

local function pass(...)
    return ...
end
check("vararg_pass", select("#", pass(1, nil, nil)), 3)
check("table_pack_n", table.pack(1, nil, 3).n, 3)
local u1, u2, u3 = table.unpack({7, 8, 9})
check("unpack", u1 + u2 + u3, 24)

local function sumAll(...: number): number
    local total = 0
    for _, v in {...} do
        total += v
    end
    return total
end
check("typed_vararg", sumAll(1, 2, 3, 4), 10)

local sx, sy = 1, 2
sx, sy = sy, sx
check("swap", sx * 10 + sy, 21)

local c = 10
c += 5
c -= 3
c *= 2
c /= 4
c //= 2
c %= 4
c ^= 2
check("compound_numeric", c, 9)
local cs = "a"
cs ..= "b"
cs ..= "c"
check("compound_concat", cs, "abc")
check("floor_division", 7 // 2 + (-7) // 2, -1)
check("number_literals", 1_000 + 0b1010 + 0xA, 1020)

local odds = {}
for i = 1, 10 do
    if i % 2 == 0 then
        continue
    end
    odds[#odds + 1] = i
end
check("continue_for", table.concat(odds, ","), "1,3,5,7,9")

local wi, wsum = 0, 0
while wi < 10 do
    wi += 1
    if wi % 3 ~= 0 then
        continue
    end
    wsum += wi
end
check("continue_while", wsum, 18)

local ri, rsum = 0, 0
repeat
    ri += 1
    if ri == 2 then
        continue
    end
    rsum += ri
until ri >= 4
check("continue_repeat", rsum, 8)

local down = {}
for i = 10, 1, -3 do
    down[#down + 1] = i
end
check("for_negative_step", table.concat(down, ","), "10,7,4,1")

local steps_f = 0
for f = 0, 1, 0.25 do
    steps_f += 1
end
check("for_float_step", steps_f, 5)

local found
for a = 1, 5 do
    for b = 1, 5 do
        if a * b == 12 then
            found = a * 10 + b
            break
        end
    end
    if found then
        break
    end
end
check("nested_break", found, 34)

local w, wsteps = 27, 0
while w ~= 1 do
    if w % 2 == 0 then
        w /= 2
    else
        w = 3 * w + 1
    end
    wsteps += 1
end
check("collatz", wsteps, 111)

local r = 0
repeat
    local sq = r * r
    r += 1
until sq >= 50
check("repeat_scope", r, 9)

local gsum = 0
for _, v in {a = 1, b = 2, c = 3} do
    gsum += v
end
check("generalized_iteration", gsum, 6)
local gorder = {}
for i, v in {"x", "y", "z"} do
    gorder[#gorder + 1] = i .. v
end
check("generalized_array", table.concat(gorder, ""), "1x2y3z")

local who, count = "world", 3
check("interpolation", `hello {who} x{count * 2}!`, "hello world x6!")
check("interpolation_escape", `\{braces\} {1 + 1}`, "{braces} 2")

local function dist2(p: Point): number
    return p.x * p.x + p.y * p.y
end
check("typed_table", dist2({x = 3, y = 4}), 25)
local function swapPair<T>(p: Pair<T>): Pair<T>
    return {first = p.second, second = p.first}
end
local sp = swapPair({first = "a", second = "b"})
check("generics", sp.first .. sp.second, "ba")
local anyv = 5 :: any
check("type_cast", (anyv :: number) + 1, 6)
check("typeof", typeof(sp) .. typeof(anyv) .. typeof(nil), "tablenumbernil")

local V = {}
V.__index = V
V.__add = function(a, b)
    return setmetatable({x = a.x + b.x}, V)
end
V.__eq = function(a, b)
    return a.x == b.x
end
V.__lt = function(a, b)
    return a.x < b.x
end
V.__len = function(a)
    return a.x
end
V.__call = function(self, k)
    return self.x * k
end
V.__tostring = function(a)
    return "V(" .. a.x .. ")"
end
V.__concat = function(a, b)
    return tostring(a) .. "|" .. tostring(b)
end
function V.new(x)
    return setmetatable({x = x}, V)
end
function V:double()
    return V.new(self.x * 2)
end
local v1, v2 = V.new(3), V.new(4)
check("meta_add", (v1 + v2).x, 7)
check("meta_eq", V.new(5) == V.new(5), true)
check("meta_lt", v1 < v2, true)
check("meta_len", #v2, 4)
check("meta_call", v1(10), 30)
check("meta_tostring", tostring(v1), "V(3)")
check("meta_concat", v1 .. v2, "V(3)|V(4)")
check("meta_method", v1:double():double().x, 12)

local proxy = setmetatable({}, {
    __index = function(_, k)
        return k .. "!"
    end,
    __newindex = function(t, k, v)
        rawset(t, k, v * 2)
    end
})
check("index_function", proxy.abc, "abc!")
proxy.n = 21
check("newindex_function", proxy.n, 42)

check("str_sub", ("hello world"):sub(-5), "world")
check("str_upper_rep", ("ab"):upper():rep(3, "-"), "AB-AB-AB")
check("str_format", string.format("%05.1f|%-4s|%x|%q", 3.14159, "ab", 255, "x"), "003.1|ab  |ff|\"x\"")
check("str_gsub_function", (("a1b22c333"):gsub("%d+", function(d)
    return "<" .. #d .. ">"
end)), "a<1>b<2>c<3>")
check("str_gsub_table", (("$x + $y"):gsub("%$(%w+)", {x = "1", y = "2"})), "1 + 2")
local words = {}
for word in ("one two  three"):gmatch("%a+") do
    words[#words + 1] = word
end
check("str_gmatch", table.concat(words, "/"), "one/two/three")
check("str_find", select(2, ("abcabc"):find("bc", 3)), 6)
check("str_char_byte", string.char(("A"):byte() + 1, 67), "BC")
check("str_escapes", #"a\0b\n\"\\\65", 7)
check("str_long", [[line1
line2]], "line1\nline2")
check("concat_numbers", 1 .. 2, "12")

check("arith", 7 % 3 + (-7) % 3 + 2 ^ 10 + 10 / 4, 1029.5)
check("precedence", 2 ^ 3 ^ 2 + -2 ^ 2, 508)
check("hex_exp", 0xFF + 1e2, 355)
check("math_lib", math.floor(-2.5) + math.max(3, 9, 4), 6)
check("math_luau", math.clamp(15, 0, 10) + math.sign(-9) + math.round(2.5), 12)
check("bit32", bit32.band(12, 10) + bit32.bor(1, 2) + bit32.bxor(5, 1) + bit32.lshift(1, 4) + bit32.rshift(256, 4), 47)
local fk = {}
fk[1.0] = "x"
check("float_key", fk[1], "x")

local function pick(a, b, c)
    return a and b or c
end
check("and_or_1", pick(true, false, "c"), "c")
check("and_or_2", pick(true, "b", "c"), "b")
check("and_or_3", pick(nil, "b", "c"), "c")
check("not_chain", not nil == true, true)

local arr = {5, 2, 9, 1}
table.sort(arr)
check("sort", table.concat(arr, ""), "1259")
table.sort(arr, function(a, b)
    return a > b
end)
check("sort_desc", table.concat(arr, ""), "9521")
table.insert(arr, 1, 7)
table.insert(arr, 3)
check("insert", table.concat(arr, ""), "795213")
check("remove", table.remove(arr, 2), 9)
check("length_after", #arr, 5)
check("table_find", table.find(arr, 2), 3)
check("table_create", #table.create(3, 0), 3)
check("table_move", table.concat(table.move({1, 2, 3}, 1, 3, 2, {9}), ","), "9,1,2,3")
local frozen = table.freeze({1, 2})
check("table_frozen", table.isfrozen(frozen), true)
check("frozen_write", (pcall(function()
    frozen[1] = 9
end)), false)
table.clear(arr)
check("table_clear", #arr, 0)

local cfg = {a = {b = {c = "deep"}}, [1] = "one", ["k k"] = 5, [2.5] = "f"}
check("nested_index", cfg.a.b.c .. cfg[1] .. cfg["k k"] .. cfg[2.5], "deepone5f")
local psum = 0
for _, v in pairs({10, 20, 30, x = 40}) do
    psum += v
end
check("pairs_sum", psum, 100)
local icount = 0
for _ in ipairs({"a", "b", nil, "d"}) do
    icount += 1
end
check("ipairs_stop", icount, 2)

local ok, err = pcall(function()
    error({code = 7})
end)
check("pcall_table", not ok and err.code, 7)
check("pcall_nil_index", (pcall(function()
    local x = nil
    return x.field
end)), false)
check("error_level_zero", select(2, pcall(error, "msg", 0)), "msg")
local _, handled = xpcall(function()
    error("bad", 0)
end, function(m)
    return "handled:" .. m
end)
check("xpcall", handled, "handled:bad")
check("xpcall_args", select(2, xpcall(function(a, b)
    return a + b
end, print, 1, 2)), 3)
check("pcall_returns", select("#", pcall(function()
    return 1, 2, 3
end)), 4)

local co = coroutine.create(function(a, b)
    local cc = coroutine.yield(a + b)
    local d = coroutine.yield(cc * 2)
    return d + 1
end)
local _, r1 = coroutine.resume(co, 1, 2)
local _, r2 = coroutine.resume(co, 10)
local _, r3 = coroutine.resume(co, 100)
check("coroutine_values", r1 .. "," .. r2 .. "," .. r3, "3,20,101")
check("coroutine_status", coroutine.status(co), "dead")
local gen = coroutine.wrap(function()
    for i = 1, 3 do
        coroutine.yield(i)
    end
end)
check("coroutine_wrap", gen() + gen() + gen(), 6)

globalCounter = 0
local function bump()
    globalCounter += 1
end
bump()
bump()
check("global_rw", globalCounter, 2)
_G["dyn_" .. "name"] = 5
check("global_dynamic", dyn_name, 5)

local spawned = false
task.spawn(function()
    spawned = true
end)
check("task_spawn", spawned, true)
task.wait()
local deferred = false
task.defer(function()
    deferred = true
end)
task.wait()
check("task_defer", deferred, true)

local seed = os.time() % 7
local choice = if seed > 3 then "high" else "low"
check("runtime_branch", choice == "high" or choice == "low", true)
local rt = {}
for i = 1, 5 do
    rt[i] = (i * seed) % 5
end
check("runtime_loop", #rt, 5)
check("main_varargs", select("#", ...) >= 0, true)

if typeof(game) == "Instance" then
    check("roblox_service", game:GetService("RunService") ~= nil, true)
    local part = Instance.new("Part")
    part.Name = "Probe"
    check("roblox_instance", part.Name, "Probe")
    part:Destroy()
end

print("passed", passed, "failed", failed)
