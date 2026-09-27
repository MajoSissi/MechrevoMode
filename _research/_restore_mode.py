# 把模式切回配置里记录的那个档位（测试脚本跑完收尾用）。
# 为什么不能写死数字：Items[0..2] 是系统自带的办公/均衡/狂暴，托盘上那三个
# 其实是 Items[3..5]（都叫 custom*），写死下标很容易把用户切到别的模式上去。
import ctypes
import json
import os
import sys
import time

u32 = ctypes.WinDLL("user32")
from _paths import CONFIG as CFG  # 数据目录见 _paths.py
from _paths import LOG  # 数据目录见 _paths.py（程序同目录\data）
WM_PROBE = 0x8004  # WM_APP + 4

cfg = json.load(open(CFG, encoding="utf-8"))
items = cfg.get("items", [])
if len(sys.argv) > 1:
    idx = int(sys.argv[1])  # 显式指定（配置里的 cur_key 可能已被测试改写）
    print("按参数切到 Items[%d] %s" % (idx, items[idx].get("name")))
else:
    key = cfg.get("cur_key")
    idx = next((i for i, it in enumerate(items) if it.get("key") == key), -1)
    print("配置记录的档位: %s -> Items[%d] %s"
          % (key, idx, items[idx].get("name") if idx >= 0 else "?"))

hwnd = u32.FindWindowW("MechrevoModeTrayWnd", None)
if not hwnd or idx < 0:
    raise SystemExit("拿不到托盘窗口或档位下标，放弃")
u32.PostMessageW(hwnd, WM_PROBE, idx, 0)
time.sleep(11)

lines = open(LOG, encoding="utf-8", errors="replace").readlines()
print("最后一条提示: %s" % [l.strip() for l in lines if "托盘提示" in l][-1].split("托盘提示 ->", 1)[1].strip())
