-- MIbandR vibe-player installer for Xiaomi Band 11 (212x520, LuaLVGL v9).
-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Derived from the Canopus Band 11 installer (AGPL-3.0, see
-- native/licenses/CANOPUS-AGPLv3-LICENSE); the CPC2 module-install stages
-- are removed and replaced by direct /dev/vibe trigger buttons.
--
-- Run performs: NSH mw/exec stage-1 -> stage-2 -> vibe-player constructor
-- (registers /dev/vibe). The trigger buttons then write one command
-- byte to /dev/vibe to play amplitude-sweep test waveforms.

-- @MIBANDR_RECOVERY@
-- @MIBANDR_PROFILE@
-- @MIBANDR_NATIVE_LOADER@
-- @MIBANDR_PROGRESS@

local PROFILE, profile_error = recovery.select_profile(PROFILES)
local recovery_ok, recovery_result, recovery_message = false, profile_error, nil
if PROFILE then
    recovery_ok, recovery_result, recovery_message = pcall(recovery.recover, PROFILE)
end
local recovered = recovery_ok and type(recovery_result) == "function"
local execute = recovered and recovery_result or nil
local recovery_error
if not recovered then recovery_error = tostring(recovery_ok and recovery_message or recovery_result) end
local control_open
if recovered and type(debug.getupvalue) == "function" then
    local _, original = debug.getupvalue(io.open, 1)
    local address = type(original) == "function" and (tostring(original):match("0[xX](%x+)$") or tostring(original):match(":%s*(%x+)$"))
    if address and (tonumber(address, 16) & ~1) == PROFILE.io_open then
        control_open = original
    end
end
local lvgl = require("lvgl")

local TARGET_ID = PROFILE and PROFILE.target_id
local VIBE_DEVICE = "/dev/vibe"
local MODULE_MIN_SIZE = 512
local MODULE_MAX_SIZE = 262144

local profile
local stage1_resource
local stage2_resource
local payload_resource
local engine_loaded = false
local run_attempted = false
local clear_armed = false
local runner, operation, current_step
local progress_count = 0
local buttons = {}
local resources_available = false
local status

local function checkpoint(text) runner.checkpoint(text) end
local function check(ok, message) if not ok then error(message, 0) end end

-- Embedded resource fallback: the Band 11 watchface runtime may not
-- materialise extraJs resources for a re-used face id; pack_face.py injects
-- base64 blobs into __MIBANDR_BLOBS_B64__ so resources are self-contained.
local MI_BANDR_EMBEDDED = __MIBANDR_BLOBS_B64__ or {}

local B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local B64_LOOKUP = {}
for i = 1, #B64_ALPHABET do B64_LOOKUP[B64_ALPHABET:sub(i, i)] = i - 1 end

local function b64decode(data)
    local out, buf, bits = {}, 0, 0
    for i = 1, #data do
        local v = B64_LOOKUP[data:sub(i, i)]
        if v then
            buf = buf * 64 + v
            bits = bits + 6
            if bits >= 8 then
                bits = bits - 8
                out[#out + 1] = string.char((buf >> bits) & 0xFF)
                buf = buf & ((1 << bits) - 1)
            end
        end
    end
    return table.concat(out)
end

local function embedded_blob(path)
    if type(MI_BANDR_EMBEDDED) ~= "table" then return nil end
    local name = tostring(path):match("([^/\\]+)$")
    local b64 = name and MI_BANDR_EMBEDDED[name]
    if type(b64) == "string" then return b64decode(b64) end
    return nil
end

local function run(command)
    print("[mibandr:vibe-player] exec: " .. command)
    if type(execute) ~= "function" then return false end
    local ok = execute(command)
    return ok == true or ok == 0
end

local function read_all(path, mode)
    if type(io) == "table" and type(io.open) == "function" then
        local file = io.open(path, mode or "rb")
        if file then
            local content = file:read("*a")
            file:close()
            return content
        end
    end
    return embedded_blob(path)
end

local function write_all(path, content)
    local file = io.open(path, "wb")
    if not file then return false end
    local write_ok, write_result = pcall(file.write, file, content)
    local close_ok, close_result = pcall(file.close, file)
    return write_ok and write_result ~= nil and close_ok and close_result ~= nil
end

local function load_profile(selected_target)
    local path = SCRIPT_PATH .. "canopus_loader_profile-" .. selected_target .. ".bin"
    local source = read_all(path, "r")
    if type(source) ~= "string" then return nil end
    local compiler = loadstring or load
    if type(compiler) ~= "function" then return nil end
    local compiled, chunk = pcall(compiler, source)
    if not compiled or type(chunk) ~= "function" then return nil end
    local loaded, value = pcall(chunk)
    if not loaded or type(value) ~= "table"
        or value.target_id ~= selected_target
        or value.status ~= "STATIC_RECOVERED"
        or type(value.device_status) ~= "string"
        or value.loader_family ~= "lua-owned-stage1-stage2" then
        return nil
    end
    return value
end

local function select_target()
    local loaded_profile = load_profile(TARGET_ID)
    if not loaded_profile then return false end
    profile = loaded_profile
    stage1_resource = SCRIPT_PATH .. "canopus_stage1-" .. TARGET_ID .. ".bin"
    stage2_resource = SCRIPT_PATH .. "canopus_stage2-" .. TARGET_ID .. ".bin"
    payload_resource = SCRIPT_PATH .. "canopus_supervisor-" .. TARGET_ID .. ".bin"
    return true
end

local function verify_payload_file(path)
    local header, size
    if type(io) == "table" and type(io.open) == "function" then
        local file = io.open(path, "rb")
        if file then
            header = file:read(20)
            size = file:seek("end")
            file:close()
        end
    end
    if not header then
        local blob = embedded_blob(path)
        if not blob then return false, "missing vibe-player payload" end
        header, size = blob:sub(1, 20), #blob
    end
    if type(header) ~= "string" or #header ~= 20
        or header:sub(1, 4) ~= "\127ELF"
        or header:byte(5) ~= 1 or header:byte(6) ~= 1
        or header:byte(7) ~= 1 then
        return false, "resource is not ELF32 little-endian"
    end
    local elf_type = header:byte(17) + header:byte(18) * 0x100
    local machine = header:byte(19) + header:byte(20) * 0x100
    if elf_type ~= 1 or machine ~= 40 then
        return false, "resource is not relocatable ARM ELF"
    end
    if type(size) ~= "number" or size < MODULE_MIN_SIZE
        or size > MODULE_MAX_SIZE then
        return false, "unexpected payload size"
    end
    return true
end

local function resources_ready()
    if type(profile) ~= "table" then return false end
    for _, item in ipairs({
        { stage1_resource, "检查第一阶段加载资源" },
        { stage2_resource, "检查第二阶段加载资源" },
        { payload_resource, "检查振动引擎资源" },
    }) do
        checkpoint(item[2])
        -- read_all falls back to embedded base64 blobs when the runtime
        -- does not materialise extraJs resources on disk.
        if type(read_all(item[1])) ~= "string" then return false end
    end
    checkpoint("检查振动引擎格式与大小")
    return verify_payload_file(payload_resource)
end

local function vibe_device_present()
    local file = control_open(VIBE_DEVICE, "rb")
    if not file then return false end
    file:close()
    return true
end

local function load_engine()
    return native_loader.load(profile, stage1_resource, stage2_resource,
        payload_resource, run, read_all, write_all, checkpoint)
end

local function vibe_send(command)
    -- Direct firmware call path: the payload registered /dev/vibe; one
    -- command byte selects the built-in waveform test.
    local file = control_open(VIBE_DEVICE, "wb")
    if not file then return false, "设备节点不可用，请先加载引擎" end
    local ok, err = pcall(file.write, file, string.pack("B", command))
    pcall(file.close, file)
    if not ok then return false, tostring(err) end
    return true
end

local function set_status(text, color)
    if status then
        -- LuaLVGL v9 on Band 11 exposes property-table setters, not
        -- set_text()/set_style_text_color() (validated on device by the
        -- Canopus installer: status:set { text = ..., text_color = ... }).
        status:set { text = tostring(text), text_color = color or '#bfd9ff' }
    end
end

local rootbase = lvgl.Object(nil, {
    w = lvgl.HOR_RES(),
    h = lvgl.VER_RES(),
    bg_color = 0,
    bg_opa = lvgl.OPA(100),
    border_width = 0,
})
rootbase:clear_flag(lvgl.FLAG.SCROLLABLE)
if lvgl.FLAG.EVENT_BUBBLE then rootbase:add_flag(lvgl.FLAG.EVENT_BUBBLE) end

local root = lvgl.Object(rootbase, {
    outline_width = 0,
    border_width = 0,
    pad_all = 0,
    bg_opa = 0,
    bg_color = 0,
    align = lvgl.ALIGN.CENTER,
    w = lvgl.HOR_RES(),
    h = lvgl.VER_RES(),
    flex = {
        flex_direction = "row",
        flex_wrap = "wrap",
        justify_content = "center",
        align_items = "center",
        align_content = "center",
    },
})
root:clear_flag(lvgl.FLAG.SCROLLABLE)
if lvgl.FLAG.EVENT_BUBBLE then root:add_flag(lvgl.FLAG.EVENT_BUBBLE) end

local title = lvgl.Label(root, {
    text_font = lvgl.Font("MiSans-Regular", 18),
    text = "MIbandR Vibe",
    text_color = '#eee',
    w = 176,
    h = 36,
    align = lvgl.ALIGN.CENTER,
})
if lvgl.FLAG.EVENT_BUBBLE then title:add_flag(lvgl.FLAG.EVENT_BUBBLE) end

status = lvgl.Label(root, {
    text_font = lvgl.Font("MiSans-Regular", 14),
    text = "执行接口已就绪\n正在准备...",
    text_color = '#bfd9ff',
    bg_color = 0,
    bg_opa = 0,
    pad_all = 4,
    w = 176,
    h = 150,
})
if lvgl.FLAG.EVENT_BUBBLE then status:add_flag(lvgl.FLAG.EVENT_BUBBLE) end

local function update_buttons()
    for _, item in ipairs(buttons) do
        if item.enable then
            if item.enable() then item.obj:clear_flag(lvgl.FLAG.HIDDEN)
            else item.obj:add_flag(lvgl.FLAG.HIDDEN) end
        end
    end
end

if lvgl.FLAG.EVENT_BUBBLE then
    rootbase:onevent(lvgl.EVENT.DELETE, function()
        runner.cancel()
        native_loader.release()
        operation = nil
    end)
end

local function make_button(text, color, on_clicked, enable)
    local button = lvgl.Object(root, {
        w = 164,
        h = 44,
        bg_color = color,
        bg_opa = lvgl.OPA(100),
    })
    button:clear_flag(lvgl.FLAG.SCROLLABLE)
    button:add_flag(lvgl.FLAG.CLICKABLE)
    if lvgl.FLAG.EVENT_BUBBLE then button:add_flag(lvgl.FLAG.EVENT_BUBBLE) end
    local label = lvgl.Label(button, {
        text_font = lvgl.Font("MiSans-Regular", 16),
        text = text,
        text_color = '#eee',
        align = lvgl.ALIGN.CENTER,
    })
    button:onevent(lvgl.EVENT.CLICKED, function(obj, code)
        local ok, message = pcall(on_clicked)
        if not ok then set_status("Error: " .. tostring(message), '#ff9a9a') end
        update_buttons()
    end)
    buttons[#buttons + 1] = { obj = button, enable = enable }
    return button, label
end

local function start_operation(name, work)
    if operation then return end
    operation, current_step, progress_count = name, nil, 0
    update_buttons()
    local ok, message = runner.start(work)
    if not ok then
        operation = nil
        set_status("无法启动：" .. tostring(message), '#ff9a9a')
        update_buttons()
    end
end

local function click_load()
    if operation or not resources_available then return end
    if run_attempted then
        set_status("单次开机仅可运行一次\n如需重新加载请先重启手环")
        return false
    end
    run_attempted = true
    start_operation("load", function()
        checkpoint("复核振动引擎文件")
        check(verify_payload_file(payload_resource))
        checkpoint("检查引擎是否已加载")
        if not vibe_device_present() then
            check(load_engine())
            checkpoint("确认引擎已就绪")
            check(vibe_device_present(), "no /dev/vibe")
        end
        engine_loaded = true
        return "引擎已加载\n可点击下方按钮测试幅度"
    end)
    return operation == "load"
end

make_button("加载引擎", '#14508a', click_load, function()
    return resources_available and not engine_loaded
end)
make_button("扫幅度", '#1a6d4a', function()
    local ok, err = vibe_send(0x01)
    check(ok, err)
    set_status("扫幅度播放中\n16/64/128/200/255\n每档 0.9s 间 0.8s", '#8ff0a4')
end, function() return engine_loaded end)
make_button("短脉冲", '#6d5a1a', function()
    local ok, err = vibe_send(0x02)
    check(ok, err)
    set_status("短脉冲播放中\n50/100/200/400 ms", '#8ff0a4')
end, function() return engine_loaded end)
make_button("恒振 3s", '#6d1a5a', function()
    local ok, err = vibe_send(0x03)
    check(ok, err)
    set_status("恒振 3 秒（对照）", '#8ff0a4')
end, function() return engine_loaded end)
make_button("停止", '#8a1f14', function()
    vibe_send(0x00)
    set_status("已停止", '#ffd27a')
end, function() return engine_loaded end)

if not recovered or not control_open then
    set_status(recovery_error and ("Startup failed: " .. recovery_error)
        or "Installer resources unavailable", '#ff9a9a')
    return
end

runner = progress.new(lvgl, function(text)
    local previous = current_step
    current_step = tostring(text)
    progress_count = progress_count + 1
    set_status(string.format("步骤 %d\n%s", progress_count, current_step)
        .. (previous and ("\n\n已完成：" .. previous) or ""))
end, function(success, message)
    local completed = operation
    operation = nil
    if completed == "startup" then resources_available = success end
    if success then
        set_status(message, '#8ff0a4')
    else
        set_status("失败步骤：" .. tostring(current_step or "启动")
            .. "\n" .. tostring(message)
            .. (completed == "load" and "\n请重启后再试" or ""), '#ff9a9a')
    end
    update_buttons()
end)

start_operation("startup", function()
    checkpoint("读取设备配置")
    check(select_target(), "Installer profile unavailable")
    check(resources_ready(), "Installer resources unavailable")
    return "Ready\n资源校验完成，点击「加载引擎」开始"
end)
