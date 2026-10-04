local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local down = {}
for i = 10, 1, -3 do
    down[#down + 1] = i
end
check("for_negative_step", table.concat(down, ","), "10,7,4,1")
local steps_f = 0
for f = 0, 1, 0.25 do
    steps_f = steps_f + 1
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
        w = w / 2
    else
        w = 3 * w + 1
    end
    wsteps = wsteps + 1
end
check("collatz", wsteps, 111)
local r = 0
repeat
    local sq = r * r
    r = r + 1
until sq >= 50
check("repeat_scope", r, 9)

print("passed", passed, "failed", failed)
