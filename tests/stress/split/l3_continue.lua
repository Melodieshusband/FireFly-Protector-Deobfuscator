local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

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
    wi = wi + 1
    if wi % 3 ~= 0 then
        continue
    end
    wsum = wsum + wi
end
check("continue_while", wsum, 18)
local ri, rsum = 0, 0
repeat
    ri = ri + 1
    if ri == 2 then
        continue
    end
    rsum = rsum + ri
until ri >= 4
check("continue_repeat", rsum, 8)

print("passed", passed, "failed", failed)
