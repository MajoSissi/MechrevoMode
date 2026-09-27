# 验证「切换模式 -> 托盘悬浮提示内容跟着变」这条完整链路：
#   Fan/Status -> formatLimits -> g.limits 变化 -> notify -> syncTray -> applyTip -> nid.Tip
#
# 提示文本本身在 Explorer 手里（悬停由 Shell 的悬浮计时驱动，合成鼠标输入触发不了，
# 也抓不到图层窗口），所以这里读程序自己落的「托盘提示 ->」日志 —— 它就在
# setTip(&nid, ...) 前一刻打印，写进 nid.Tip 的就是这一行。
#
# 需要**提权**：wmProbe 是发给提权进程的自定义消息，UIPI 会吞掉普通进程发来的消息。
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)

WM_APP = 0x8000
WM_PROBE = WM_APP + 4
from _paths import CONFIG, LOG  # 数据目录见 _paths.py（程序同目录\data）

u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u32.FindWindowW.restype = wintypes.HWND
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def last_tip():
    """返回 (行号, 文本)；文本是 %q 转义过的一行。"""
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return -1, "<日志打不开: %s>" % e
    for i in range(len(lines) - 1, -1, -1):
        if "托盘提示 ->" in lines[i]:
            return i, lines[i].split("托盘提示 ->", 1)[1].strip()
    return -1, "<找不到>"


def main():
    hwnd = u32.FindWindowW("MechrevoModeTrayWnd", None)
    if not hwnd:
        print("!! 找不到托盘窗口（程序没在跑？）", flush=True)
        return 1

    fails = []
    line0, tip0 = last_tip()
    print("切换前提示（日志第 %d 行）= %s" % (line0, tip0), flush=True)

    # 收尾要切回去的那个下标，必须在改动之前就读出来 ——
    # 跑完一趟配置里的 cur_key 已经变了，事后再读只会读到测试自己的结果。
    # 另外别想当然用 0：Items[0..2] 是系统自带的办公/均衡/狂暴，
    # 托盘上那三项其实是 Items[3..5]，写死 0 会把用户切到别的模式上。
    restore = 0
    cfg = {"items": []}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        restore = next((i for i, it in enumerate(cfg.get("items", []))
                        if it.get("key") == cfg.get("cur_key")), 0)
    except Exception as e:
        print("  (读配置拿恢复下标失败，退回 0：%s)" % e, flush=True)
    print("  收尾将恢复 Items[%d]" % restore, flush=True)

    # 逐一切到托盘菜单上真正会出现的那几项（show_tray=true），
    # 拿不到就退回系统三段 0/1/2 —— 它们三档的限制值差别最大，最看得出联动
    ports = [i for i, it in enumerate(cfg.get("items", [])) if it.get("show_tray")]
    if len(ports) < 2:
        ports = [0, 1, 2]
    print("  将依次切到 Items%s" % ports, flush=True)

    seen = {}
    for port in ports:
        u32.PostMessageW(hwnd, WM_PROBE, port, 0)
        time.sleep(10.0)  # GCU 会先推一条旧状态，实测要等 9 秒以上才是新值
        ln, tip = last_tip()
        print("  切到档位 %d -> 提示 = %s" % (port, tip), flush=True)
        seen[port] = tip
        # 注意：第一个档位就是当前档位时不会有新日志（状态没变当然不刷新），
        # 这不算失败 —— 「提示跟着模式走」由下面「至少出现两种不同文本」来判定。
        del ln
        # 结构约定：两行，形如
        #   CPU - 85°C - 38/38/45W
        #   GPU - 87°C - 50W
        lines = tip.strip('"').split("\\n")
        ok = (len(lines) == 2
              and lines[0].startswith("CPU - ")
              and lines[1].startswith("GPU - "))
        if ok:
            # 分隔符只有两处，不允许出现「 - 」结尾或连续分隔符
            ok = all(" - " in ln and not ln.endswith(" - ") and " -  - " not in ln for ln in lines)
        if not ok:
            fails.append("档位 %d 的提示不是两行「CPU/GPU - 温度 - 功耗」结构：%s" % (port, tip))
        if any(n in tip for n in ("静音", "均衡", "狂暴", "自定义", "办公", "未连接")):
            fails.append("档位 %d 的提示里混进了模式名：%s" % (port, tip))
        line0 = last_tip()[0]

    if len(set(seen.values())) < 2:
        fails.append("三个档位的提示完全一样，说明没跟着模式走：%s" % seen)

    # 收尾：切回跑测试之前那个档位
    u32.PostMessageW(hwnd, WM_PROBE, restore, 0)
    time.sleep(10.0)
    print("收尾：切回 Items[%d] -> 提示 = %s" % (restore, last_tip()[1]), flush=True)

    print("-" * 62, flush=True)
    if fails:
        for f in fails:
            print("FAIL: " + f, flush=True)
        return 1
    print("=> PASS：提示随模式变化，始终是两行「CPU/GPU - 温度 - 功耗」且不含模式名", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
