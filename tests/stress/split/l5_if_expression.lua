local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local function sgn(x)
    return if x > 0 then 1 elseif x < 0 then -1 else 0
end
check("if_expression", sgn(9) * 100 + sgn(-4) * 10 + sgn(0), 90)
local choice = if sgn(3) == 1 then "pos" else "other"
check("if_expression_value", choice, "pos")

print("passed", passed, "failed", failed)
