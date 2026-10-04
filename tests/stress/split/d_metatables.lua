local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local V = {}
V.__index = V
V.__add = function(a, b)
    return setmetatable({x = a.x + b.x}, V)
end
V.__eq = function(a, b)
    return a.x == b.x
end
V.__lt = function(a, b)
    return a.x < b.x
end
V.__call = function(self, k)
    return self.x * k
end
V.__tostring = function(a)
    return "V(" .. a.x .. ")"
end
V.__concat = function(a, b)
    return tostring(a) .. "|" .. tostring(b)
end
function V.new(x)
    return setmetatable({x = x}, V)
end
function V:double()
    return V.new(self.x * 2)
end
local v1, v2 = V.new(3), V.new(4)
check("meta_add", (v1 + v2).x, 7)
check("meta_eq", V.new(5) == V.new(5), true)
check("meta_lt", v1 < v2, true)
check("meta_call", v1(10), 30)
check("meta_tostring", tostring(v1), "V(3)")
check("meta_concat", v1 .. v2, "V(3)|V(4)")
check("meta_method", v1:double():double().x, 12)
local proxy = setmetatable({}, {
    __index = function(_, k)
        return k .. "!"
    end,
    __newindex = function(t, k, v)
        rawset(t, k, v * 2)
    end
})
check("index_function", proxy.abc, "abc!")
proxy.n = 21
check("newindex_function", proxy.n, 42)

print("passed", passed, "failed", failed)
