# 端到端验证悬浮提示的转速那两行：往 broker 上**注入**一条 System/FanInfo，
# 看程序（pid 46552 那份安装版）会不会把它吃进去并刷新托盘提示。
#
# 为什么要注入：GCU 自己的遥测推送目前是停的（_sysinfo_verify.py 连着 30 秒 0 条），
# 拿不到真报文就没法证明「主题名对不对 / 字段名对不对 / 拼出来的文本对不对」。
# 注入能一次性覆盖订阅、解析、快照、拼文本、2 秒重绘这整条链。
import socket
import struct
import sys
import time

HOST, PORT = "127.0.0.1", 13688
TOPIC = "System/FanInfo"


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

    # 四拍，每拍换一组值：既证明数值真的被读进来了，也证明 2 秒节拍在动
    shots = [
        '{"CpuFanDuty":55,"GpuFanDuty":55,"CpuFanRpm":3111,"GpuFanRpm":2222}',
        '{"CpuFanDuty":60,"GpuFanDuty":60,"CpuFanRpm":4222,"GpuFanRpm":3333}',
        '{"CpuFanDuty":45,"GpuFanDuty":45,"CpuFanRpm":1855,"GpuFanRpm":933}',
        '{"CpuFanDuty":70,"GpuFanDuty":70,"CpuFanRpm":0,"GpuFanRpm":0}',
    ]
    for i, body in enumerate(shots, 1):
        publish(s, TOPIC, body)
        print("  第 %d 拍 已发布 %s" % (i, body), flush=True)
        time.sleep(5)  # 留足两个 2 秒节拍，确保程序一定重绘过
    s.close()
    print("注入结束", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
