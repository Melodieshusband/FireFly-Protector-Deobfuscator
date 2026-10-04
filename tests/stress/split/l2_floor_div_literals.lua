local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

check("floor_division", 7 // 2 + (-7) // 2, -1)
check("number_literals", 1_000 + 0b1010 + 0xA, 1020)

print("passed", passed, "failed", failed)
