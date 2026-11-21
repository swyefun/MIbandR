#!/usr/bin/env python3
"""Post-process a generated module-installer watchface main.lua.

The pinned Canopus generator (1) starts the install as soon as the watchface
is opened, and (2) creates full-screen LVGL objects without EVENT_BUBBLE,
which swallows the system long-press gesture used to open the watchface
switcher.

This patch:
  * replaces the auto-start with an explicit "安装模块" button,
  * keeps the status/progress display and friendly Supervisor diagnostics,
  * adds EVENT_BUBBLE to every root/label object, matching both official
    Canopus watchfaces (manager activator and framework installer).

Each anchor is applied independently, so a file already patched by an older
version of this script still receives the newer fixes.

Usage: python3 patch_installer_button.py <main.lua> [<main.lua> ...]
"""
import sys
from pathlib import Path

STATUS_OLD = '''local status = lvgl.Label(root, {
    text = "准备安装...", text_color = 0xBFD9FF,
    text_font = lvgl.Font("MiSans-Regular", width < 300 and 14 or 18),
    w = width - 32, h = height - 146,'''
STATUS_NEW = '''local status = lvgl.Label(root, {
    text = "点击「安装模块」开始", text_color = 0xBFD9FF,
    text_font = lvgl.Font("MiSans-Regular", width < 300 and 14 or 18),
    w = width - 32, h = height - 190,'''

DETAIL_OLD = 'local function failure_detail(response) return " error " .. u32(response, 36) end'
DETAIL_NEW = '''local function failure_detail(response)
    local code = u32(response, 36)
    if code >= 0x80000000 then code = code - 0x100000000 end
    if code == -105 then return "：模块已是最新版本，无需重复安装" end
    if code == -109 then return "：已安装版本更新，拒绝降级" end
    return " error " .. code
end'''

TAIL_OLD = '''local ok, message = runner.start(install)
if not ok then fail(message) end'''
TAIL_NEW = '''-- Explicit install button: opening the watchface must not install anything.
local install_button = lvgl.Object(root, {
    w = width - 48, h = 48,
    bg_color = 0x14508A, bg_opa = lvgl.OPA(100), border_width = 0,
    align = { type = lvgl.ALIGN.TOP_MID, x_ofs = 0, y_ofs = height - 68 },
})
install_button:clear_flag(lvgl.FLAG.SCROLLABLE)
install_button:add_flag(lvgl.FLAG.CLICKABLE)
if lvgl.FLAG.EVENT_BUBBLE then install_button:add_flag(lvgl.FLAG.EVENT_BUBBLE) end
lvgl.Label(install_button, {
    text = "安装模块", text_color = 0xFFFFFF,
    text_font = lvgl.Font("MiSans-Regular", 16),
    align = lvgl.ALIGN.CENTER,
})
local install_started = false
install_button:onevent(lvgl.EVENT.CLICKED, function()
    if install_started then return end
    install_started = true
    install_button:clear_flag(lvgl.FLAG.CLICKABLE)
    local ok, message = runner.start(install)
    if not ok then
        install_started = false
        install_button:add_flag(lvgl.FLAG.CLICKABLE)
        fail(message)
    end
end)'''

# EVENT_BUBBLE fixes. Both official Canopus watchfaces bubble events from
# every full-screen object so the system gesture layer still receives long
# presses. Without this the watchface switcher cannot be opened from the face.
BUBBLE_ROOT_OLD = '''rootbase:clear_flag(lvgl.FLAG.SCROLLABLE)
root:clear_flag(lvgl.FLAG.SCROLLABLE)'''
BUBBLE_ROOT_NEW = '''rootbase:clear_flag(lvgl.FLAG.SCROLLABLE)
if lvgl.FLAG.EVENT_BUBBLE then rootbase:add_flag(lvgl.FLAG.EVENT_BUBBLE) end
root:clear_flag(lvgl.FLAG.SCROLLABLE)
if lvgl.FLAG.EVENT_BUBBLE then root:add_flag(lvgl.FLAG.EVENT_BUBBLE) end'''

BUBBLE_TITLE_OLD = '''lvgl.Label(root, {
    text = CONFIG.title, text_color = 0xFFFFFF,
    text_font = lvgl.Font("MiSans-Regular", width < 300 and 18 or 24),
    w = width - 32, h = 48,
    align = { type = lvgl.ALIGN.TOP_MID, x_ofs = 0, y_ofs = 52 },
})'''
BUBBLE_TITLE_NEW = '''local title = lvgl.Label(root, {
    text = CONFIG.title, text_color = 0xFFFFFF,
    text_font = lvgl.Font("MiSans-Regular", width < 300 and 18 or 24),
    w = width - 32, h = 48,
    align = { type = lvgl.ALIGN.TOP_MID, x_ofs = 0, y_ofs = 52 },
})
if lvgl.FLAG.EVENT_BUBBLE then title:add_flag(lvgl.FLAG.EVENT_BUBBLE) end'''

BUBBLE_STATUS_OLD = '''    align = { type = lvgl.ALIGN.TOP_MID, x_ofs = 0, y_ofs = 106 },
})
local function fail(message)'''
BUBBLE_STATUS_NEW = '''    align = { type = lvgl.ALIGN.TOP_MID, x_ofs = 0, y_ofs = 106 },
})
if lvgl.FLAG.EVENT_BUBBLE then status:add_flag(lvgl.FLAG.EVENT_BUBBLE) end
local function fail(message)'''

# (name, old, new, marker) -- marker present means this fix is already applied
PATCHES = (
    ('status-text', STATUS_OLD, STATUS_NEW, '点击「安装模块」开始'),
    ('signed-diagnostics', DETAIL_OLD, DETAIL_NEW, 'code >= 0x80000000'),
    ('install-button', TAIL_OLD, TAIL_NEW, 'install_button'),
    ('bubble-roots', BUBBLE_ROOT_OLD, BUBBLE_ROOT_NEW,
     'rootbase:add_flag(lvgl.FLAG.EVENT_BUBBLE)'),
    ('bubble-title', BUBBLE_TITLE_OLD, BUBBLE_TITLE_NEW,
     'local title = lvgl.Label(root'),
    ('bubble-status', BUBBLE_STATUS_OLD, BUBBLE_STATUS_NEW,
     'status:add_flag(lvgl.FLAG.EVENT_BUBBLE)'),
)


def patch(path):
    text = path.read_text(encoding='utf-8')
    applied = []
    for name, old, new, marker in PATCHES:
        if marker in text:
            continue
        if text.count(old) != 1:
            raise SystemExit('%s: [%s] anchor not found exactly once: %r'
                             % (path, name, old[:60]))
        text = text.replace(old, new, 1)
        applied.append(name)
    if applied:
        path.write_text(text, encoding='utf-8', newline='\n')
        return 'patched: ' + ', '.join(applied)
    return 'already up to date'


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.is_file():
            raise SystemExit('missing file: ' + str(path))
        print('%s: %s' % (path, patch(path)))


if __name__ == '__main__':
    main()
