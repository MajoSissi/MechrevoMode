# 找出「实时遥测」到底怎么触发。
#
# 已知：GCUService 的二进制里有 System/CpuInfo、System/GpuInfo、System/FanInfo 这几个
# 主题常量，也有 FanInfoTimer / StartTimers 和 GetEcCpuFanRpm / GetCpuTemperature 这些函数，
# 但被动订阅 70 秒一条都没见着 —— 说明定时器没在跑，多半要等客户端先开口。
# 所以这里把可能的动作名逐个试一遍，看谁能让它开始推。
#
# 只发 GET/START 类动作，不碰任何 SET/DELETE，不会改动机器状态。
import json
import re
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688

ACTIONS = [
    "GETSTATUS", "GETINFO", "GET_INFO", "GETSUPPORT",
    "GETSYSTEMINFO", "GETHARDWAREINFO", "GETHWINFO",
    "GETCPUINFO", "GETGPUINFO", "GETFANINFO",
    "START", "STARTTIMER", "STARTMONITOR", "STARTHWINFO",
    "STARTSYSTEMMONITOR", "ENABLEMONITOR", "ENABLE",
    "SUBSCRIBE", "INIT", "INITIALIZE",
]

# 同一个动作分别往这些控制主题发（命名规律是 <类别>/Control）
TARGETS = ["System/Control", "SystemMonitor/Control", "GamingMonitor/Control", "Hardware/Control"]


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


def drain(s, seconds, sink, tag):
    """收 seconds 秒，把 (topic, payload) 塞进 sink（按 topic 去重留最新）。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = read_packet(s, max(0.05, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        data = r[2]
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        sink[topic] = (data[2 + tl:], tag)


def main():
    s = None
    for i in range(1, 10):
        s = connect(i)
        if s:
            print("已连接 clientID=UWPClient_%d" % i, flush=True)
            break
    if not s:
        print("!! 1..9 号 clientID 全被占用", flush=True)
        return 2

    sub = struct.pack(">H", 1) + enc_str("#") + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub)) + sub)
    read_packet(s, 5)

    # 基线：什么都不发，先看有没有自己会来的
    base = {}
    drain(s, 3.0, base, "baseline")
    print("基线 %.0f 秒内话题: %s" % (3.0, sorted(base) or "（无）"), flush=True)

    # 逐个动作试
    print("\n开始逐个试动作（每个动作收集 2.5 秒）…", flush=True)
    hits = {}
    for act in ACTIONS:
        for tgt in TARGETS:
            publish(s, tgt, json.dumps({"Action": act}).encode())
        time.sleep(0.15)
        sink = {}
        drain(s, 2.5, sink, act)
        new = {k: v for k, v in sink.items() if k not in base and "/Control" not in k}
        if new:
            for k, (raw, _) in new.items():
                base[k] = (raw, act)
                hits[k] = act
            print("  %-22s -> %s" % (act, sorted(new)), flush=True)

    s.close()
    print("\n" + "=" * 74)
    print("全部新增话题: %s" % (sorted(hits) or "（无 —— 这些动作都唤不动它）"))
    print("=" * 74)

    KW = re.compile(r"rpm|temp|fan|power|watt|freq|usage|duty|load", re.I)
    if hits:
        print("\n内容里含遥测关键词的话题:")
        for t in sorted(hits):
            raw = base[t][0].decode("utf-8", "replace")
            print("\n---- [%s]  由 %s 触发" % (t, hits[t]))
            print(raw[:1500])
    else:
        print("\n线索：")
        print("  * 可能动作名不在候选里（.NET 里是拼出来的，字符串 dump 看不到）")
        print("  * 或者需要先让某个客户端「声明订阅」才会开定时器")
        print("  * 退路：官方/第三方控制台的监控页开着时再嗅一遍，看它发了什么")

    # 顺带把「含遥测关键词」的既有话题也印出来，便于比对
    print("\n" + "=" * 74)
    print("基线话题里含遥测关键词的字段（供比对）:")
    found = False
    for t in sorted(base):
        try:
            obj = json.loads(base[t][0].decode("utf-8"))
        except Exception:
            continue
        if isinstance(obj, dict):
            for k, v in obj.items():
                if KW.search(k):
                    print("  [%s] %s = %r" % (t, k, v))
                    found = True
    if not found:
        print("  （无）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
