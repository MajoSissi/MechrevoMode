# -*- coding: utf-8 -*-
"""找出「谁在往 broker 发遥测」—— 直接看 13688 上的 TCP 连接归属。

之前那次端口表扫描有两处错误，导致结论不可信：
  1. MIB_TCPROW_OWNER_PID 是 **6 个 DWORD（24 字节）**，我按 7 个解了，行错位；
  2. 端口字段是**网络字节序的低 16 位**，必须先 ntohs 再比对。

这次先自己起一个订阅者占住连接作为**对照**：如果连自己的连接都扫不出来，
说明解析还是错的，不能拿「没扫到」当结论。
"""

import ctypes
import socket
import struct
import threading
import time
from ctypes import wintypes

HOST, PORT = "127.0.0.1", 13688

iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")


# ---------------------------------------------------------------- 进程名表

def process_names():
    size = 1 << 22
    while True:
        buf = ctypes.create_string_buffer(size)
        ret = ctypes.c_ulong(0)
        st = ntdll.NtQuerySystemInformation(5, buf, size, ctypes.byref(ret))
        if st == 0xC0000004:
            size *= 2
            continue
        if st != 0:
            raise OSError(hex(st))
        break
    blob = buf.raw
    off = 0
    out = {}
    while True:
        nxt = struct.unpack_from("<I", blob, off)[0]
        pid = struct.unpack_from("<Q", blob, off + 0x50)[0]
        ln = struct.unpack_from("<H", blob, off + 0x38)[0]
        bp = struct.unpack_from("<Q", blob, off + 0x40)[0]
        out[pid] = (ctypes.string_at(bp, ln).decode("utf-16-le", "replace")
                    if ln and bp else "")
        if nxt == 0:
            break
        off += nxt
    return out


def ntohs16(v):
    p = v & 0xFFFF
    return ((p & 0xFF) << 8) | (p >> 8)


def tcp_rows(af):
    """返回 [(state, localport, remoteport, pid)]"""
    cls = 5                      # TCP_TABLE_OWNER_PID_ALL
    size = wintypes.DWORD(0)
    iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, af, cls, 0)
    if size.value == 0:
        return [], 0
    buf = ctypes.create_string_buffer(size.value)
    r = iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, af, cls, 0)
    if r != 0:
        raise OSError(f"GetExtendedTcpTable(af={af}) 失败 {r}")
    n = struct.unpack_from("<I", buf.raw, 0)[0]
    rowsize = 24 if af == 2 else 56
    out = []
    for i in range(n):
        f = struct.unpack_from("<IIIIII", buf.raw, 4 + i * rowsize)
        out.append((f[0], ntohs16(f[2]), ntohs16(f[4]), f[5]))
    return out, n


# ---------------------------------------------------------------- 对照用订阅者

def dummy_subscriber(ready, stop):
    """占一条真实连接，用来验证扫描器本身管用。"""
    try:
        s = socket.create_connection((HOST, PORT), timeout=5)
    except OSError as e:
        ready.set()
        return
    vh = (b"\x00\x04MQTT\x04\xc2\x00\x19")
    pid = b"UWPClient_9"
    pay = (struct.pack(">H", len(pid)) + pid
           + struct.pack(">H", len(b"UWPClient_User_9")) + b"UWPClient_User_9"
           + struct.pack(">H", len(b"UWPClient_Pwd888881772688_9")) + b"UWPClient_Pwd888881772688_9")
    body = vh + pay
    s.sendall(bytes([0x10, len(body)]) + body)
    time.sleep(0.4)
    s.recv(16)
    ready.set()
    while not stop.is_set():
        time.sleep(0.2)
    s.close()


def main():
    names = process_names()
    ready = threading.Event()
    stop = threading.Event()
    th = threading.Thread(target=dummy_subscriber, args=(ready, stop), daemon=True)
    th.start()
    ready.wait(6)
    print("对照组（本脚本自己的 MQTT 连接）已建立，开始扫描…\n")

    for i in range(3):
        print(f"---- 第 {i + 1} 轮 ----")
        for af, label in ((2, "IPv4"), (23, "IPv6")):
            try:
                rows, total = tcp_rows(af)
            except OSError as e:
                print(f"  {label}: {e}")
                continue
            hit = [r for r in rows if r[1] == PORT or r[2] == PORT]
            print(f"  {label}: 表内共 {total} 行，涉及 {PORT} 的 {len(hit)} 条")
            for state, lp, rp, pid in hit:
                st = {1: "CLOSED", 2: "LISTEN", 3: "SYN_SENT", 4: "SYN_RCVD",
                      5: "ESTABLISHED", 6: "FIN_WAIT1", 7: "FIN_WAIT2",
                      8: "CLOSE_WAIT", 9: "CLOSING", 10: "LAST_ACK",
                      11: "TIME_WAIT", 12: "DELETE_TCB"}.get(state, str(state))
                print(f"      {st:<12} local={lp:<6} remote={rp:<6} "
                      f"pid={pid:<6} {names.get(pid, '?')}")
        time.sleep(1.5)

    stop.set()


if __name__ == "__main__":
    main()
