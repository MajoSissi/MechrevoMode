# -*- coding: utf-8 -*-
"""在 OEM 二进制里搜 MQTT 动作名 / 主题名，找出「武装遥测」的那条命令。

GCU 的通信都是 {"Action":"XXX"} 或 <主题>/<子项> 形式，所以直接在映像里
找可打印字符串最省事：比抓包更快，而且不用去动用户的机器。

用法: python _strings_scan.py <文件或目录> [...]
"""

import os
import re
import sys

# 值得关注的模式
PATTERNS = [
    re.compile(rb"System/[A-Za-z]{3,20}"),
    re.compile(rb"[A-Za-z]{3,20}/(Control|Status|Info)"),
    re.compile(rb'"Action"\s*:\s*"[^"]{1,40}"'),
    re.compile(rb"Action\s*=\s*[\"']?[A-Za-z_]{3,40}"),
]

# 含这些词的整串才算「和遥测/风扇有关」，否则噪音太大
KEYWORDS = [b"Fan", b"Monitor", b"Rpm", b"RPM", b"Telemetry", b"Start", b"Stop",
            b"Enable", b"Disable", b"System/", b"Ec", b"EC"]

ACTIONS = re.compile(rb"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+){0,5}\b")

TARGETS_CORE = rb"fan|monitor|rpm|telemetr|temperature|speed|watch"


def is_ascii_printable(b):
    return all(32 <= c < 127 for c in b)


def extract_strings(blob, minlen=4):
    """扫出 ASCII 与 UTF-16LE 两种编码的字符串，返回 (offset, text)。"""
    out = []
    # ASCII
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, blob):
        out.append((m.start(), m.group().decode("ascii", "replace"), "ascii"))
    # UTF-16LE
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % minlen, blob):
        out.append((m.start(), m.group().decode("utf-16-le", "replace"), "utf16"))
    return out


def scan_file(path):
    try:
        blob = open(path, "rb").read()
    except OSError as e:
        print(f"  读取失败 {path}: {e}")
        return
    name = os.path.basename(path)
    strings = extract_strings(blob)

    hits = {"主题名": set(), "Action 字面量": set(), "疑似动作常量": set()}

    for off, s, enc in strings:
        sb = s.encode("utf-8", "replace")
        low = sb.lower()
        # 主题名
        for p in PATTERNS[:2]:
            m = p.search(sb)
            if m and m.group() != sb and "/" in s:
                hits["主题名"].add(s.strip())
        # Action 字面量
        if '"Action"' in s or "Action=" in s or "Action :" in s:
            for am in ACTIONS.finditer(sb):
                hits["Action 字面量"].add(am.group().decode())
        # 疑似动作常量：全大写+下划线，且带 Fan/Monitor/Rpm 等词
        if re.search(TARGETS_CORE, low) and re.fullmatch(r"[A-Z][A-Z0-9_]{4,44}", s):
            hits["疑似动作常量"].add(s)

    # 只保留和遥测/风扇沾边的主题名，否则会刷屏
    topics = {t for t in hits["主题名"]
              if re.search(r"fan|monitor|system|control|info", t, re.I)}
    topics = {t for t in topics if len(t) < 40}

    if not (topics or hits["Action 字面量"] or hits["疑似动作常量"]):
        print(f"  [{name}] 无命中")
        return

    print(f"  [{name}]  {len(blob)} 字节")
    if topics:
        print("     主题名:")
        for t in sorted(topics):
            print("       ", t)
    if hits["Action 字面量"]:
        print("     Action 字面量:")
        for t in sorted(hits["Action 字面量"])[:25]:
            print("       ", t)
    if hits["疑似动作常量"]:
        print("     疑似动作常量:")
        for t in sorted(hits["疑似动作常量"])[:35]:
            print("       ", t)


def main():
    targets = sys.argv[1:]
    if not targets:
        print(__doc__)
        return 2
    for t in targets:
        if os.path.isdir(t):
            print(f"=== 目录 {t} ===")
            for f in sorted(os.listdir(t)):
                fp = os.path.join(t, f)
                if os.path.isfile(fp) and f.lower().endswith((".exe", ".dll")):
                    scan_file(fp)
        else:
            print(f"=== 文件 {t} ===")
            scan_file(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
