local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local arr = {7, 5, 2, 1, 3}
check("table_find", table.find(arr, 2), 3)
check("table_create", #table.create(3, 0), 3)
check("table_move", table.concat(table.move({1, 2, 3}, 1, 3, 2, {9}), ","), "9,1,2,3")
local frozen = table.freeze({1, 2})
check("table_frozen", table.isfrozen(frozen), true)
check("frozen_write", (pcall(function()
    frozen[1] = 9
end)), false)
table.clear(arr)
check("table_clear", #arr, 0)
check("math_luau", math.clamp(15, 0, 10) + math.sign(-9) + math.round(2.5), 12)
check("bit32", bit32.band(12, 10) + bit32.bor(1, 2) + bit32.bxor(5, 1) + bit32.lshift(1, 4) + bit32.rshift(256, 4), 47)
check("table_pack_n", table.pack(1, nil, 3).n, 3)

print("passed", passed, "failed", failed)
