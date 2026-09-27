# 打开 GCU 的实时遥测流（System/Control + System_ON），完整抓一段时间。
#
# 结论（已实测）：System_ON 是开关；打开后会周期性推送
#   System/CpuInfo    CpuUsage / CpuTemperature / CpuFrequency / CpuMaxFrequency
#   System/GpuInfo    GpuUsage / GpuTemperature / GpuCoreFreq / GpuMemFreq / GpuPState / GpuMem
#   System/FanInfo    CpuFanDuty / GpuFanDuty / CpuFanRpm / GpuFanRpm
#   System/MemoryInfo / System/NetworkInfo / System/BatteryInfo / System/HardwareInfo / System/FanErrorInfo
# 本脚本用来核对「有没有漏掉的 topic / 字段」（尤其功耗），并量出真实推送间隔。
#
# 收尾一定发 System_OFF，别把这个流一直开着。
import json
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688


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


def publish(s, topic, body):
    pv = enc_str(topic)
    b = body.encode("utf-8")
    s.sendall(bytes([0x30]) + enc_len(len(pv) + len(b)) + pv + b)


def main():
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0

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
    print("已连接 UWPClient_%d" % used, flush=True)

    payload = struct.pack(">H", 1) + enc_str("#") + bytes([0])
    s.sendall(bytes([0x82]) + enc_len(len(payload)) + payload)
    read_packet(s, 5)

    # 排掉订阅瞬间的 retained
    drain = time.time() + 2.0
    while time.time() < drain:
        if not read_packet(s, max(0.05, drain - time.time())):
            break

    publish(s, "System/Control", '{"Action":"System_ON"}')
    print("已发 System_ON，抓 %.0f 秒…\n" % wait, flush=True)

    seen = {}
    deadline = time.time() + wait
    while time.time() < deadline:
        r = read_packet(s, max(0.05, deadline - time.time()))
        if not r or r[0] != 3:
            continue
        _t, _f, data = r
        tl = struct.unpack(">H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        seen.setdefault(topic, []).append((time.time(), data[2 + tl:]))

    publish(s, "System/Control", '{"Action":"System_OFF"}')
    time.sleep(0.4)
    s.close()
    print("已发 System_OFF 收尾\n", flush=True)

    print("=" * 78)
    print("%-30s %-6s %-12s %s" % ("topic", "条数", "间隔", "最新内容"))
    print("-" * 78, flush=True)
    for t in sorted(seen):
        ts = [x[0] for x in seen[t]]
        gap = ("%.2f 秒" % ((ts[-1] - ts[0]) / (len(ts) - 1))) if len(ts) > 1 else "-"
        body = seen[t][-1][1].decode("utf-8", "replace")
        print("%-30s %-6d %-12s %s" % (t, len(ts), gap, body[:150]), flush=True)

    print("\n所有出现过的字段名：", flush=True)
    keys = set()
    for t in seen:
        try:
            obj = json.loads(seen[t][-1][1].decode("utf-8"))
        except Exception:
            continue
        if isinstance(obj, dict):
            for k in obj:
                keys.add((t, k))
    for t, k in sorted(keys):
        print("  [%s] %s" % (t, k), flush=True)

    with open("_system_stream.txt", "w", encoding="utf-8") as f:
        for t in sorted(seen):
            f.write("---- [%s] ×%d\n" % (t, len(seen[t])))
            for ts, raw in seen[t][-3:]:
                f.write("  %s  %s\n" % (time.strftime("%H:%M:%S", time.localtime(ts)),
                                        raw.decode("utf-8", "replace")))
            f.write("\n")
    print("\n完整内容（每个 topic 最后 3 条）已写入 _system_stream.txt", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
