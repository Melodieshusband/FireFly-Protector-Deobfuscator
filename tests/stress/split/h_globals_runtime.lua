local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

globalCounter = 0
local function bump()
    globalCounter = globalCounter + 1
end
bump()
bump()
check("global_rw", globalCounter, 2)
_G["dyn_" .. "name"] = 5
check("global_dynamic", dyn_name, 5)
local seed = os.time() % 7
local choice = seed > 3 and "high" or "low"
check("runtime_branch", choice == "high" or choice == "low", true)
local rt = {}
for i = 1, 5 do
    rt[i] = (i * seed) % 5
end
check("runtime_loop", #rt, 5)
check("main_varargs", select("#", ...) >= 0, true)

print("passed", passed, "failed", failed)
