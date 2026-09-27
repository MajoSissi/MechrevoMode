# 试着用 EnableLocalMonitoring 之类动作把实时遥测（System/CpuInfo 等）打开。
#
# 线索来源：官方控制台字符串里同时出现了
#   主题订阅表  System/CpuInfo / System/GpuInfo / System/FanInfo / ...
#   字段名      CpuTemperature / CpuFanRpm / GpuFanRpm / CpuFanDuty ...
#   动作名      EnableLocalMonitoring / DisableLocalMonitoring / RefreshLocalMonitoring
# 而被动监听 45 秒什么都收不到 —— 说明要先发动作把它打开。
#
# 每个动作单独试，中间留观察窗，报出「哪个动作带来哪些新 topic」。
import json
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688

# (发布到哪个主题, 动作体)
TRIALS = [
    ("System/Control", '{"Action":"System_ON"}'),
    ("System/Control", '{"Action":"System"}'),
    ("System/Control", '{"Action":"ON"}'),
    ("System/Control", '{"Action":"EnableLocalMonitoring"}'),
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
    s = socket.create_connection((HOST, PORT), timeout=5)
    vh = enc_str("MQTT") + bytes([0x04]) + bytes([0xC2]) + struct.pack(">H", 25)
    payload = (enc_str("UWPClient_%d" % index) + enc_str("UWPClient_User_%d" % index)
               + enc_str("UWPClient_Pwd888881772688_%d" % index))
    s.sendall(bytes([0x10]) + enc_len(len(vh) + len(payload)) + vh + payload)
    r = read_packet(s, 5)
    if not r or r[0] != 2 or r[2][1] != 0:
        s.close()
        return None
    return s


def main():
    window = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

    s = None
    used = -1
    for i in range(1, 12):
        s = connect(i)
        if s:
            used = i
            break
    if not s:
        print("!! 没有空闲 clientID", flush=True)
        return 2
    print("已连接 UWPClient_%d\n" % used, flush=True)

    payload = struct.pack(">H", 1) + enc_str("#") + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(payload)) + payload)
    read_packet(s, 5)

    # 先排掉订阅瞬间的 retained 消息，免得混进来干扰判断
    drain_until = time.time() + 2.0
    while time.time() < drain_until:
        if not read_packet(s, max(0.05, drain_until - time.time())):
            break

    def pump(sec):
        """收集 sec 秒，返回 {topic: [payload...]}"""
        got = {}
        end = time.time() + sec
        while time.time() < end:
            r = read_packet(s, max(0.05, end - time.time()))
            if not r or r[0] != 3:
                continue
            _t, _f, data = r
            tl = struct.unpack(">H", data[:2])[0]
            topic = data[2:2 + tl].decode("utf-8", "replace")
            got.setdefault(topic, []).append(data[2 + tl:])
        return got

    for topic, body in TRIALS:
        pv = enc_str(topic)
        b = body.encode("utf-8")
        s.sendall(bytes([0x30]) + enc_len(len(pv) + len(b)) + pv + b)
        got = pump(window)
        watch = [t for t in got if t.startswith("System/") or "Info" in t]
        print("-- 发 %s -> %s" % (topic, body), flush=True)
        if not got:
            print("     （无任何回包）", flush=True)
        for t in sorted(got):
            mark = " ★" if t in watch else ""
            print("     %-32s ×%-3d %s%s" % (t, len(got[t]), got[t][-1][:180].decode("utf-8", "replace"), mark), flush=True)
        print(flush=True)

    # 收尾：关掉监控，别留着
    body = b'{"Action":"DisableLocalMonitoring"}'
    pv = enc_str("System/Control")
    s.sendall(bytes([0x30]) + enc_len(len(pv) + len(body)) + pv + body)
    time.sleep(0.5)
    s.close()
    print("已发 DisableLocalMonitoring 收尾", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
