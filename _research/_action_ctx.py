# -*- coding: utf-8 -*-
"""围绕 `"Action"` 与主题名的上下文转储 —— 用来找 GCU 支持哪些动作。

之前那版把主题名和动作常量混在一起、又被 head 截断，看不到重点。
这一版只做两件事：
  1. 列出所有形如 Xxx/Yyy 的主题名（以及 "System/..." 的完整清单）
  2. 对每个 `"Action"` 出现处，把前后各 300 字节里的可打印串全部打出来 —— 
     动作名通常是紧挨着 "Action" 的字符串常量。
"""

import re
import sys

DEFAULT = [r"C:\Program Files\OEM\机械革命控制中心\AiStoneService\MyControlCenter\GCUService.exe",
           r"C:\Program Files\OEM\机械革命控制中心\AiStoneService\GCUBridge.exe",
           r"C:\Program Files\OEM\机械革命控制中心\GamingCenter\ControlCenterU.exe",
           r"C:\Program Files\OEM\机械革命控制中心\GamingCenter\GamingCenterU.exe"]

TOPIC = re.compile(rb"\b[A-Z][A-Za-z0-9_]{2,20}/[A-Z][A-Za-z0-9_]{2,20}\b")
ACTION_LIT = re.compile(rb"[\"']Action[\"']")
STR_LIT = re.compile(rb"[\x20-\x7e]{3,60}")


def printable_strings(blob, lo, hi):
    out = []
    for m in STR_LIT.finditer(blob, max(0, lo), min(len(blob), hi)):
        out.append(m.group().decode("ascii", "replace"))
    return out


def scan(path):
    try:
        blob = open(path, "rb").read()
    except OSError as e:
        print(f"  读取失败 {path}: {e}")
        return
    name = path.rsplit("\\", 1)[-1]

    # ---- 1. 主题名
    topics = sorted({m.group().decode() for m in TOPIC.finditer(blob)})
    print(f"\n=== {name}  ({len(blob)} 字节) ===")
    print(f"  疑似主题名 {len(topics)} 个：")
    for i in range(0, len(topics), 4):
        print("    " + "  ".join(f"{t:<26}" for t in topics[i:i + 4]))

    # ---- 2. Action 上下文
    hits = list(ACTION_LIT.finditer(blob))
    print(f"  `\"Action\"` 出现 {len(hits)} 次；每处附近的可打印串：")
    shown = 0
    seen_ctx = set()
    for h in hits:
        ctx = printable_strings(blob, h.start() - 260, h.start() + 340)
        # 只留长度像「标识符」的，滤掉路径/日志噪音
        cand = [c for c in ctx
                if 3 <= len(c) <= 40
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ ]*", c)
                and " " not in c.strip()]
        key = tuple(cand)
        if not cand or key in seen_ctx:
            continue
        seen_ctx.add(key)
        shown += 1
        if shown > 30:
            break
        print("    @0x%08X: %s" % (h.start(), " | ".join(cand)))
    if shown == 0:
        print("    （附近没有成形的动作名 —— 说明动作名不是字面量，可能是枚举/拼接出来的）")


def main():
    for p in (sys.argv[1:] or DEFAULT):
        scan(p)


if __name__ == "__main__":
    main()
