local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local gsum = 0
for _, v in {a = 1, b = 2, c = 3} do
    gsum = gsum + v
end
check("generalized_iteration", gsum, 6)
local gorder = {}
for i, v in {"x", "y", "z"} do
    gorder[#gorder + 1] = i .. v
end
check("generalized_array", table.concat(gorder, ""), "1x2y3z")

print("passed", passed, "failed", failed)
