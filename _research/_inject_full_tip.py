# 完整端到端验证：先注入一条真机 Fan/Status（限制信息那两行的来源），
# 再注入 System/FanInfo，看悬浮提示是不是变成用户要的那四行、中间没有分隔线。
#
# Fan/Status 用的是 limits_test.go 里那份真机报文（自定义 Profile1）。
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688

RAW_FAN_STATUS = (
    '{"IsAC":true,"OperatingMode":"3","CustomProfileIndex":"0","ProfileName":"Mode4_Profile1",'
    '"CPU_PL1":"75","CPU_PL2":"85","CPU_PL4":"85","CPU_AmdSPL":"38","CPU_AmdSPPT":"38",'
    '"CPU_AmdFPPT":"45","CPU_AmdTccTarget":"85","CPU_TccOffsetSwitch":"1","TjMax":"95",'
    '"CPU_TccOffset":"95","GPU_TargetTemperature":"87","GPU_ConfigurableTGPTarget":"50",'
    '"GPU_ConfigurableTGPSwitch":"1","GPU_DynamicBoost":"5","GPU_DynamicBoostSwitch":"0",'
    '"IsAMDPlatform":true,"IsNvGpu":true}'
)


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
    p = enc_str(topic) + body.encode("utf-8")
    s.sendall(bytes([0x30]) + enc_len(len(p)) + p)


def main():
    s = None
    for i in range(1, 10):
        s = connect(i)
        if s:
            print("已连接 clientID=UWPClient_%d" % i, flush=True)
            break
    if not s:
        print("!! 无空闲 clientID", flush=True)
        return 2

    publish(s, "Fan/Status", RAW_FAN_STATUS)
    print("  已发布 Fan/Status（限制两行的来源）", flush=True)
    time.sleep(6)  # 让程序接住并重绘

    for rpm_c, rpm_g in ((2990, 2854), (4120, 3760)):
        body = ('{"CpuFanDuty":55,"GpuFanDuty":55,"CpuFanRpm":%d,"GpuFanRpm":%d}' % (rpm_c, rpm_g))
        publish(s, "System/FanInfo", body)
        print("  已发布 System/FanInfo %d / %d" % (rpm_c, rpm_g), flush=True)
        time.sleep(5)

    s.close()
    print("注入结束", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
