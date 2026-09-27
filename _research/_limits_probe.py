# 逐个切换模式，抓每个模式下的 Fan/Status，对比「功耗墙/温度墙」字段如何变化。
#
# 为什么必须做这一步：报文里有**两套** CPU 功耗字段
#   * CPU_PL1 / CPU_PL2 / CPU_PL4 + TjMax          （Intel 风格）
#   * CPU_AmdSPL / CPU_AmdSPPT / CPU_AmdFPPT + CPU_AmdTccTarget （AMD 风格）
# 本机 IsAMDPlatform=true，但两套都有值且不一致（PL1=75 而 AmdSPL=38）。
# 「哪个随模式变」才能说明哪个是真正生效的那套 —— 拍脑袋选会显示错数据。
#
# 脚本结束会把模式恢复成运行前的状态。
import json
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688
INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 3
CLIENT_ID = "UWPClient_%d" % INDEX
USERNAME = "UWPClient_User_%d" % INDEX
PASSWORD = "UWPClient_Pwd888881772688_%d" % INDEX
TOPIC_CTRL, TOPIC_STAT = "Fan/Control", "Fan/Status"

MODES = [
    ("静音(办公)", '{"Action":"OPERATING_OFFICE_MODE"}'),
    ("均衡(游戏)", '{"Action":"OPERATING_GAMING_MODE"}'),
    ("狂暴", '{"Action":"OPERATING_TURBO_MODE"}'),
    ("自定义0", '{"Action":"OPERATING_CUSTOM_MODE","ProfileIndex":0}'),
    ("自定义1", '{"Action":"OPERATING_CUSTOM_MODE","ProfileIndex":1}'),
]

CPU_PWR = ["CPU_PL1", "CPU_PL2", "CPU_PL4",
           "CPU_AmdSPL", "CPU_AmdSPPT", "CPU_AmdFPPT"]
CPU_TMP = ["TjMax", "CPU_TccOffset", "CPU_TccOffsetSwitch", "CPU_AmdTccTarget",
           "CPU_TccOffsetMaximum"]
GPU_PWR = ["GPU_ConfigurableTGPTarget", "GPU_ConfigurableTGPSwitch",
           "GPU_DynamicBoost", "GPU_DynamicBoostSwitch"]
GPU_TMP = ["GPU_TargetTemperature"]
WATCH = CPU_PWR + CPU_TMP + GPU_PWR + GPU_TMP + ["OperatingMode", "ProfileName"]


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


class Cli:
    def __init__(self):
        self.s = socket.create_connection((HOST, PORT), timeout=5)
        vh = enc_str("MQTT") + bytes([0x04]) + bytes([0xC2]) + struct.pack(">H", 25)
        pl = enc_str(CLIENT_ID) + enc_str(USERNAME) + enc_str(PASSWORD)
        self.s.sendall(bytes([0x10]) + enc_len(len(vh) + len(pl)) + vh + pl)
        r = read_packet(self.s, 5)
        if not r or r[0] != 2 or r[2][1] != 0:
            raise SystemExit("CONNACK 失败 %s" % (r,))
        sp = struct.pack(">H", 1) + enc_str(TOPIC_STAT) + bytes([0])
        self.s.sendall(bytes([0x82]) + enc_len(len(sp)) + sp)
        if not read_packet(self.s, 5):
            raise SystemExit("SUBACK 超时")

    def pub(self, body):
        pv = enc_str(TOPIC_CTRL)
        self.s.sendall(bytes([0x30]) + enc_len(len(pv) + len(body)) + pv + body.encode())

    def drain(self, seconds):
        """持续收 seconds 秒，返回最后一条 Fan/Status 的 dict"""
        last = None
        end = time.time() + seconds
        while time.time() < end:
            r = read_packet(self.s, max(0.1, end - time.time()))
            if not r or r[0] != 3:
                continue
            d = r[2]
            tl = struct.unpack(">H", d[:2])[0]
            topic = d[2:2 + tl].decode("utf-8", "replace")
            if topic != TOPIC_STAT:
                continue
            try:
                last = json.loads(d[2 + tl:].decode("utf-8"))
            except Exception:
                pass
        return last

    def close(self):
        self.s.close()


def main():
    c = Cli()
    c.pub('{"Action":"GETSTATUS"}')
    base = c.drain(3.0)
    if not base:
        raise SystemExit("收不到 Fan/Status（后端没起来？）")

    restore_mode = base.get("OperatingMode", "3")
    restore_prof = base.get("CustomProfileIndex", "0")
    base_name = base.get("ProfileName", "?")
    print("起始状态 OperatingMode=%s ProfileName=%s" % (restore_mode, base_name), flush=True)
    print("", flush=True)

    rows = []
    for label, payload in MODES:
        c.pub(payload)
        time.sleep(1.2)
        st = c.drain(3.5)
        if not st:
            st = base
        rows.append((label, st))
        print("%-10s -> mode=%s %s" % (label, st.get("OperatingMode"), st.get("ProfileName")),
              flush=True)

    # 恢复
    if restore_mode == "3":
        c.pub('{"Action":"OPERATING_CUSTOM_MODE","ProfileIndex":%s}' % restore_prof)
    elif restore_mode == "2":
        c.pub('{"Action":"OPERATING_TURBO_MODE"}')
    elif restore_mode == "1":
        c.pub('{"Action":"OPERATING_GAMING_MODE"}')
    else:
        c.pub('{"Action":"OPERATING_OFFICE_MODE"}')
    time.sleep(1.5)
    c.drain(3.0)
    c.close()

    print("", flush=True)
    print("已恢复到 OperatingMode=%s" % restore_mode, flush=True)
    print("", flush=True)

    def table(title, keys):
        print("### %s" % title, flush=True)
        hdr = "%-12s" % "模式" + "".join("%-14s" % k.replace("CPU_", "").replace("GPU_", "")
                                        for k in keys)
        print(hdr, flush=True)
        print("-" * len(hdr.encode("gbk", "replace")), flush=True)
        for label, st in rows:
            line = "%-12s" % label
            for k in keys:
                v = st.get(k, "-")
                line += "%-14s" % v
            print(line, flush=True)
        print("", flush=True)

    table("CPU 功耗（W?）", CPU_PWR)
    table("CPU 温度（°C?）", CPU_TMP)
    table("GPU 功耗（W?）", GPU_PWR)
    table("GPU 温度（°C?）", GPU_TMP)

    # 结论：哪些字段在不同模式之间有差异 = 真正受模式控制
    print("### 判定：哪些字段随模式变化", flush=True)
    for k in CPU_PWR + CPU_TMP + GPU_PWR + GPU_TMP:
        vals = {str(st.get(k, "-")) for _l, st in rows}
        print("  %-45s %s  值集合=%s" % (k, "★随模式变" if len(vals) > 1 else "  恒定", sorted(vals)),
              flush=True)


if __name__ == "__main__":
    main()
