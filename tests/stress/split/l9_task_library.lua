local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

local spawned = false
task.spawn(function()
    spawned = true
end)
check("task_spawn", spawned, true)
task.wait()
local deferred = false
task.defer(function()
    deferred = true
end)
task.wait()
check("task_defer", deferred, true)

print("passed", passed, "failed", failed)
