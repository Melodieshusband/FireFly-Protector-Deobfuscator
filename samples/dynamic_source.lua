local seed = os.time() % 7
local t = {}
for i = 1, 5 do
    t[i] = (i * seed) % 5
end
if seed > 3 then
    print("high")
else
    print("low")
end
print(#t, #string.rep("x", seed + 1) >= 1)
print(select("#", ...))
print(_G["pri" .. "nt"] == print)
print(type(_G), type(math.random(1, 10)))
local loader = loadstring or load
print(loader and loader("return 1 + 1")() or "no loader")
local data = {}
for i = 1, 3 do
    data[#data + 1] = tostring(os.clock()):sub(1, 1) ~= "" and i or 0
end
print(table.concat(data, ","))
