local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

type Point = {x: number, y: number}
type Pair<T> = {first: T, second: T}
local function dist2(p: Point): number
    return p.x * p.x + p.y * p.y
end
check("typed_table", dist2({x = 3, y = 4}), 25)
local function swapPair<T>(p: Pair<T>): Pair<T>
    return {first = p.second, second = p.first}
end
local sp = swapPair({first = "a", second = "b"})
check("generics", sp.first .. sp.second, "ba")
local anyv = 5 :: any
check("type_cast", (anyv :: number) + 1, 6)
check("typeof", typeof(sp) .. typeof(anyv) .. typeof(nil), "tablenumbernil")
local function sumAll(...: number): number
    local total = 0
    for _, v in {...} do
        total = total + v
    end
    return total
end
check("typed_vararg", sumAll(1, 2, 3, 4), 10)

print("passed", passed, "failed", failed)
