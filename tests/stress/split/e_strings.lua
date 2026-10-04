local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

check("str_sub", ("hello world"):sub(-5), "world")
check("str_upper_rep", ("ab"):upper():rep(3, "-"), "AB-AB-AB")
check("str_format", string.format("%05.1f|%-4s|%x|%q", 3.14159, "ab", 255, "x"), "003.1|ab  |ff|\"x\"")
check("str_gsub_function", (("a1b22c333"):gsub("%d+", function(d)
    return "<" .. #d .. ">"
end)), "a<1>b<2>c<3>")
check("str_gsub_table", (("$x + $y"):gsub("%$(%w+)", {x = "1", y = "2"})), "1 + 2")
local words = {}
for word in ("one two  three"):gmatch("%a+") do
    words[#words + 1] = word
end
check("str_gmatch", table.concat(words, "/"), "one/two/three")
check("str_find", select(2, ("abcabc"):find("bc", 3)), 6)
check("str_char_byte", string.char(("A"):byte() + 1, 67), "BC")
check("str_escapes", #"a\0b\n\"\\\65", 7)
check("str_long", [[line1
line2]], "line1\nline2")
check("concat_numbers", 1 .. 2, "12")

print("passed", passed, "failed", failed)
