# 找出 GCU broker 上「实时遥测」（温度 / 功耗 / 风扇转速）到底在哪个 topic。
#
# 背景：Fan/Status 里只有各种**限制值和开关**（CPU_PL1、CPU_AmdTccTarget、GPU_TargetTemperature…），
# 一条实时读数都没有。但 SupportInfo 里有 SystemMonitorSupport=1，说明另有监控主题。
#
# 做法：通配符订阅 '#'，然后往一批候选的 <类别>/Control 发 GETSTATUS，看谁会回状态。
# 同时观察「不提问也自己周期性发布」的 topic —— 实时遥测很可能属于这一类。
#
# 自包含（不 import 别的脚本：那些脚本会在 import 时读 sys.argv / 跑副作用）。
import json
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688

# 候选控制主题。命名规律是 <类别>/Control -> <类别>/Status。
#
# System/Control 这一组是从官方控制台的字符串里挖出来的（_us_ccu_clean.txt），
# 里面能看到 System/CpuInfo、System/GpuInfo、System/FanInfo 等主题名 ——
# 实时遥测就在这一组，之前靠猜 "<类别>/Control" 是找不到的。
CANDIDATES = [
    "System/Control",
    "Fan/Control",
    "SystemMonitor/Control",
    "Monitor/Control",
    "Hardware/Control",
    "Settings/Control",
    "Setting/Control",
]


def enc_len(n):
    out = b""
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out += bytes([b])
        if n == 0:
            return out


def enc_str(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def read_packet(sock, timeout):
    sock.settimeout(timeout)
    try:
        head = sock.recv(1)
        if not head:
            return None
        mult, val = 1, 0
        for _ in range(4):
            b = sock.recv(1)[0]
            val += (b & 0x7F) * mult
            if not (b & 0x80):
                break
            mult *= 128
        data = b""
        while len(data) < val:
            chunk = sock.recv(val - len(data))
            if not chunk:
                break
            data += chunk
        return head[0] >> 4, head[0] & 0x0F, data
    except (socket.timeout, OSError):
        return None


def connect(index):
    """连上并返回 socket；失败返回 None。"""
    cid = "UWPClient_%d" % index
    s = socket.create_connection((HOST, PORT), timeout=5)
    vh = enc_str("MQTT") + bytes([0x04]) + bytes([0xC2]) + struct.pack(">H", 25)
    payload = (enc_str(cid) + enc_str("UWPClient_User_%d" % index)
               + enc_str("UWPClient_Pwd888881772688_%d" % index))
    s.sendall(bytes([0x10]) + enc_len(len(vh) + len(payload)) + vh + payload)
    r = read_packet(s, 5)
    if not r or r[0] != 2 or r[2][1] != 0:
        s.close()
        return None
    return s


def publish(s, topic, body):
    pv = enc_str(topic)
    s.sendall(bytes([0x30]) + enc_len(len(pv) + len(body)) + pv + body)


def main():
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0

    s = None
    used = -1
    for i in range(1, 10):
        s = connect(i)
        if s:
            used = i
            break
    if not s:
        print("!! 1..9 号 clientID 全被占用", flush=True)
        return 2
    print("已连接 clientID=UWPClient_%d" % used, flush=True)

    sub = struct.pack(">H", 1) + enc_str("#") + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub)) + sub)
    r = read_packet(s, 5)
    print("SUBACK rc=%s" % (r[2][2] if r and len(r[2]) >= 3 else "?",), flush=True)

    # 每个候选主题发一次 GETSTATUS
    for t in CANDIDATES:
        publish(s, t, b'{"Action":"GETSTATUS"}')
        time.sleep(0.12)
    print("已向 %d 个候选主题发 GETSTATUS，收集 %.0f 秒…\n" % (len(CANDIDATES), wait), flush=True)

    seen = {}      # topic -> [(payload, 时间)]
    withget = {}   # topic -> 最近一次 payload
    deadline = time.time() + wait
    while time.time() < deadline:
        r = read_packet(s, max(0.1, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        _typ, _fl, data = r
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        raw = data[2 + tl:]
        seen.setdefault(topic, []).append((raw, time.time()))
        withget[topic] = raw

    s.close()

    print("=" * 70)
    print("topic 清单（出现次数 / 字节数）：")
    for t in sorted(seen):
        print("  %-34s ×%-4d %d 字节" % (t, len(seen[t]), len(seen[t][-1][0])))
    print()

    # 重点：找带数字读数的字段。键名里含 temp / rpm / speed / power / fan 的都列出来
    print("=" * 70)
    print("疑似遥测字段（键名含 temp/rpm/speed/fan/power/watt/load/usage/clock）：")
    KEYWORDS = ("temp", "rpm", "speed", "fan", "power", "watt", "load", "usage", "clock", "duty")
    hit = False
    for t in sorted(withget):
        try:
            obj = json.loads(withget[t].decode("utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        for k, v in obj.items():
            if any(w in k.lower() for w in KEYWORDS):
                print("  [%s] %s = %r" % (t, k, v))
                hit = True
    if not hit:
        print("  （无）")

    # 周期性发布的 topic：实时遥测的典型特征
    print()
    print("=" * 70)
    print("发布频率（用于判断谁在周期性推送）：")
    for t in sorted(seen):
        ts = [x[1] for x in seen[t]]
        if len(ts) > 1:
            span = ts[-1] - ts[0]
            print("  %-34s %d 条 / %.1f 秒（约 %.2f 秒一条）"
                  % (t, len(ts), span, span / (len(ts) - 1)))
        else:
            print("  %-34s 只出现 1 次" % t)

    out = "_sniff_telemetry.txt"
    with open(out, "w", encoding="utf-8") as f:
        for t in sorted(withget):
            f.write("---- [%s]\n" % t)
            try:
                f.write(json.dumps(json.loads(withget[t].decode("utf-8")),
                                   ensure_ascii=False, indent=2))
            except Exception:
                f.write(withget[t].decode("utf-8", "replace"))
            f.write("\n\n")
    print("\n各 topic 最后一次的完整内容已写入 %s" % out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
