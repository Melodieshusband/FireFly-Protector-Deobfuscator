local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local who, count = "world", 3
check("interpolation", `hello {who} x{count * 2}!`, "hello world x6!")
check("interpolation_escape", `\{braces\} {1 + 1}`, "{braces} 2")

print("passed", passed, "failed", failed)
