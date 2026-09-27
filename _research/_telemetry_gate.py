# 探测「GCU 什么时候才推 System/* 遥测」。
#
# 背景：16:59 那一轮 _sysinfo_verify.py 明明收到了 CpuInfo/FanInfo/GpuInfo（2 秒一条），
# 到了 17:10 同样的脚本一条都收不到，而 Fan/Status（GETSTATUS 的直接应答）一直正常。
# 说明遥测是**条件性**发布的 —— 要么和 clientID 序号有关，要么和某个开关动作有关。
#
# 这个脚本逐个序号试一遍：每个序号只订阅三个 Info 主题，观察若干秒，数条数。
import ctypes
import json
import struct
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 13688
WATCH = ["System/CpuInfo", "System/FanInfo", "System/GpuInfo"]
PER_INDEX = 6.0
INDICES = [0, 1, 2, 3, 4, 5]


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


def publish(s, topic, payload):
    t = enc_str(topic)
    body = t + payload.encode("utf-8")
    s.sendall(bytes([0x30]) + enc_len(len(body)) + body)


def subscribe(s, topics):
    sub = struct.pack(">H", len(topics))
    for t in topics:
        sub += enc_str(t) + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(sub)) + sub)
    return read_packet(s, 5)


def watch(index, seconds, kick_off=False, actions=None):
    s = connect(index)
    if not s:
        return None, None, None
    subscribe(s, WATCH)
    if actions:
        for act in actions:
            publish(s, "System/Control", json.dumps({"Action": act}))
    counts = {t: 0 for t in WATCH}
    samples = {}
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = read_packet(s, max(0.05, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        data = r[2]
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        if topic in counts:
            counts[topic] += 1
            try:
                samples[topic] = json.loads(data[2 + tl:].decode("utf-8"))
            except Exception:
                pass
    s.close()
    return counts, samples, True


def procs():
    TH32CS_SNAPPROCESS = 0x2
    MAX_PATH = 260

    class PE32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * MAX_PATH),
        ]

    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    out = []
    ok = k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
    while ok:
        out.append((e.th32ProcessID, e.szExeFile))
        ok = k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
    k.CloseHandle(ctypes.c_void_p(snap))
    return out


def main():
    pl = procs()
    print("=== 相关进程 ===")
    for name in ("GCUBridge.exe", "GCUService.exe", "L-Mechrevo.exe",
                 "SystrayComponent.exe", "MechrevoMode.exe", "OSDTpDetect.exe"):
        hit = [p for p, n in pl if n.lower() == name.lower()]
        print("  %-22s %s" % (name, hit or "-"))

    print("\n=== 逐个 clientID 序号试（每个 %0.f 秒，只订阅 3 个 Info 主题）===" % PER_INDEX)
    for idx in INDICES:
        counts, samples, ok = watch(idx, PER_INDEX)
        if not ok:
            print("  index %-2d 连不上" % idx)
            continue
        total = sum(counts.values())
        tag = "有遥测" if total else "**没有**"
        print("  index %-2d 共 %2d 条  %s   %s" % (idx, total, tag, counts))
        if samples:
            for t, o in samples.items():
                print("        %-18s %s" % (t, json.dumps(o, ensure_ascii=False)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
