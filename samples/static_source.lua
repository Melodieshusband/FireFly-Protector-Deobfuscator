local unpack = table.unpack or unpack

local function fib(n)
    if n < 2 then return n end
    return fib(n - 1) + fib(n - 2)
end

local function counter(step)
    local total = 0
    return function(...)
        for i = 1, select("#", ...) do
            total = total + select(i, ...) * step
        end
        return total
    end
end

local Vec = {}
Vec.__index = Vec
Vec.__add = function(a, b)
    return setmetatable({x = a.x + b.x, y = a.y + b.y}, Vec)
end
Vec.__tostring = function(v)
    return "Vec(" .. v.x .. "," .. v.y .. ")"
end
Vec.__call = function(v, k)
    return v.x * k, v.y * k
end
function Vec.new(x, y)
    return setmetatable({x = x, y = y}, Vec)
end
function Vec:len2()
    return self.x * self.x + self.y * self.y
end

local acc = counter(3)
acc(1, 2, 3)
print("counter", acc(4))

local fibs = {}
for i = 0, 10 do
    fibs[#fibs + 1] = fib(i)
end
print("fib", table.concat(fibs, ","))

local a, b = Vec.new(1, 2), Vec.new(3, 4)
print(tostring(a + b), (a + b):len2(), (a + b)(2))

local fns = {}
for i = 1, 3 do
    fns[i] = function()
        i = i + 10
        return i
    end
end
print(fns[1](), fns[1](), fns[2](), fns[3]())

local words = {}
for w in ("the quick brown fox jumps"):gmatch("%a+") do
    words[#words + 1] = w:upper()
end
table.sort(words, function(p, q)
    return #p < #q or (#p == #q and p < q)
end)
print(table.concat(words, " "))

local map = {}
for k, v in ("key1=val1;key2=val2;key3=val3"):gmatch("(%w+)=(%w+)") do
    map[k] = v
end
local keys = {}
for k in pairs(map) do
    keys[#keys + 1] = k
end
table.sort(keys)
for _, k in ipairs(keys) do
    print(k, map[k])
end

local n, steps = 27, 0
while n ~= 1 do
    if n % 2 == 0 then
        n = n / 2
    else
        n = 3 * n + 1
    end
    steps = steps + 1
end
print("collatz", steps)

local grid = {}
for r = 1, 3 do
    grid[r] = {}
    for c = 1, 3 do
        if r == c then
            grid[r][c] = 1
        elseif r < c then
            grid[r][c] = 2
        else
            grid[r][c] = 3
        end
    end
end
local rows = {}
for r = 1, 3 do
    rows[#rows + 1] = table.concat(grid[r], "")
end
print("grid", table.concat(rows, "/"))

local j = 0
repeat
    j = j + 1
    if j % 2 == 0 then
        print("even", j)
    end
until j >= 5

local ok, err = pcall(function()
    error("boom")
end)
print("pcall", ok, (tostring(err):gsub("^.-:%d+: ", "")))

print(select("#", unpack({1, 2, nil, 4}, 1, 4)))
print(string.format("%05.1f|%-4s|%x", 3.14159, "ab", 255))
print(("abc"):rep(3), ("hello"):sub(2, -2), ("hello"):byte(1, 2))
print(string.char(72, 105), #"\65\066\x43", "tab\there", [[long
string]])
print(0x10, 1e3, 2 ^ 10, 7 % 3, -7 % 3, 10 / 4, math.floor(-2.5), math.max(1, 9, 4))
print(tostring(nil), tonumber("0x1F"), tonumber("  12  "), tonumber("z", 36), #{1, 2, 3, n = 4})

local wait = wait or function() end
wait(0.1)
print("done")
