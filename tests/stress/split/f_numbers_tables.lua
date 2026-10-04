local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

check("arith", 7 % 3 + (-7) % 3 + 2 ^ 10 + 10 / 4, 1029.5)
check("precedence", 2 ^ 3 ^ 2 + -2 ^ 2, 508)
check("hex_exp", 0xFF + 1e2, 355)
check("math_lib", math.floor(-2.5) + math.max(3, 9, 4), 6)
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
local cfg = {a = {b = {c = "deep"}}, [1] = "one", ["k k"] = 5, [2.5] = "f"}
check("nested_index", cfg.a.b.c .. cfg[1] .. cfg["k k"] .. cfg[2.5], "deepone5f")
local psum = 0
for _, v in pairs({10, 20, 30, x = 40}) do
    psum = psum + v
end
check("pairs_sum", psum, 100)
local icount = 0
for _ in ipairs({"a", "b", nil, "d"}) do
    icount = icount + 1
end
check("ipairs_stop", icount, 2)

print("passed", passed, "failed", failed)
