local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

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

print("passed", passed, "failed", failed)
