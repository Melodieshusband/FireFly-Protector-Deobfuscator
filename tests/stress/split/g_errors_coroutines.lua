local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

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

print("passed", passed, "failed", failed)
