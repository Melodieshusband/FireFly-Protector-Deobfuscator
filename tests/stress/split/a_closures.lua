local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local fns = {}
for i = 1, 3 do
    fns[i] = function()
        i = i + 10
        return i
    end
end
check("loop_closure_1", fns[1](), 11)
check("loop_closure_1b", fns[1](), 21)
check("loop_closure_2", fns[2](), 12)
local makers = {}
for i = 1, 2 do
    local base = i * 100
    for j = 1, 2 do
        makers[#makers + 1] = function()
            base = base + j
            return base
        end
    end
end
check("nested_closures", makers[1]() .. "," .. makers[2]() .. "," .. makers[3]() .. "," .. makers[4](), "101,103,201,203")
local function counter()
    local n = 0
    return function()
        n = n + 1
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
local function adder(a)
    return function(b)
        return function(c)
            return a + b + c
        end
    end
end
check("curry", adder(1)(2)(3), 6)

print("passed", passed, "failed", failed)
