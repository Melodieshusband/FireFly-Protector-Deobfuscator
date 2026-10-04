local passed, failed = 0, 0

local function check(name, got, expected)
    if got == expected then
        passed = passed + 1
    else
        failed = failed + 1
        print("FAIL", name, tostring(got), tostring(expected))
    end
end

check("roblox_service", game:GetService("RunService") ~= nil, true)
local part = Instance.new("Part")
part.Name = "Probe"
check("roblox_instance", part.Name, "Probe")
part:Destroy()

print("passed", passed, "failed", failed)
