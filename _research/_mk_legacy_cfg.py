import json, os, shutil, datetime

d = os.path.join(os.environ['APPDATA'], 'MechrevoMode')
p = os.path.join(d, 'config.json')
if os.path.exists(p):
    shutil.copy2(p, p + '.bak')
    print("已备份 ->", p + '.bak')

BAL  = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIG  = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
PURP = 0x9B51E0

def it(key, mode, slot, name, tray, color, glyph, plan):
    return {"key": key, "mode": mode, "slot": slot, "name": name,
            "show_tray": tray, "icon_color": color, "icon_glyph": glyph,
            "power_plan": plan}

items = [
    # 故意用最早的旧默认：办公/O/蓝，均衡/B/绿，游戏狂暴/G/红
    it("office",    0, -1, "办公",       True, 0x2F80ED, "O", BAL),
    it("balance",   1, -1, "均衡",       True, 0x27AE60, "B", BAL),
    it("turbo",     2, -1, "游戏狂暴",   True, 0xE2445C, "G", HIG),
]
for i in range(5):
    items.append(it(f"custom{i+1}", 3, i, f"自定义 {i+1}", True, PURP, str(i+1), BAL))

legacy = {
    "version": 5,
    "auto_start": False,
    "client_index": -1,
    "cur_key": "balance",
    "items": items,
    "run_enabled": False,
    "run_path": r"C:/Windows/System32/cmd.exe",
    "run_args": "/c echo RUNNOW_OK",
    "run_delay_sec": 0,
    "run_elevate": False,
    "plan_alias": {},
}
with open(p, 'w', encoding='utf-8') as f:
    json.dump(legacy, f, ensure_ascii=False, indent=2)
print("已写入旧版配置:")
for x in items:
    print(f"  {x['key']:<9} name={x['name']:<8} glyph={x['icon_glyph']} color=#{x['icon_color']:06X}")
