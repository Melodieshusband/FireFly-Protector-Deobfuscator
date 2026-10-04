local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local function fact(n)
    if n <= 1 then
        return 1
    end
    return n * fact(n - 1)
end
check("recursion", fact(10), 3628800)
local isodd
local function iseven(n)
    if n == 0 then
        return true
    end
    return isodd(n - 1)
end
function isodd(n)
    if n == 0 then
        return false
    end
    return iseven(n - 1)
end
check("mutual_even", iseven(10), true)
check("mutual_odd", isodd(7), true)
local function mix(a, b)
    return a * 10 + b
end
check("args_a", mix(1, 2), 12)
check("args_b", mix(3, 4), 34)
local function classify(n)
    if n < 0 then
        return "neg"
    elseif n == 0 then
        return "zero"
    elseif n < 10 then
        return "small"
    else
        return "large"
    end
end
local classes = {}
for _, n in ipairs({-5, 0, 7, 50}) do
    classes[#classes + 1] = classify(n)
end
check("elseif_chain", table.concat(classes, ","), "neg,zero,small,large")
local function find_pair(target)
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
local sx, sy = 1, 2
sx, sy = sy, sx
check("swap", sx * 10 + sy, 21)

print("passed", passed, "failed", failed)
