# 把 System/* 的**全部**主题常量都订阅一遍，看哪些真的会推数据。
# 目的：确认「功耗」这个字段是不是藏在某个我们还没看过的主題里。
import socket, struct, sys, time, json
HOST, PORT = "127.0.0.1", 13688
TOPICS = [
    "System/Control", "System/FanErrorInfo", "System/FanInfo", "System/BatteryInfo",
    "System/CpuInfo", "System/StaticsData", "System/MemoryInfo", "System/GpuInfo",
    "System/IGpuInfo", "System/NetworkInfo", "System/DiskInfo", "System/HardwareInfo",
    "System/HwFuelGauge", "System/BatteryProtection",
    "Monitor/Status", "GamingMonitor/Status", "GPUDevice/Status", "Fan/Table",
]

def enc_len(n):
    out=b""
    while True:
        b=n%128; n//=128
        if n>0: b|=0x80
        out+=bytes([b])
        if n==0: return out
def enc_str(s):
    b=s.encode("utf-8"); return struct.pack(">H",len(b))+b
def read_packet(sock, timeout):
    sock.settimeout(timeout)
    try:
        head=sock.recv(1)
        if not head: return None
        mult,val=1,0
        for _ in range(4):
            b=sock.recv(1)[0]; val+=(b&0x7F)*mult
            if not (b&0x80): break
            mult*=128
        data=b""
        while len(data)<val:
            c=sock.recv(val-len(data))
            if not c: break
            data+=c
        return head[0]>>4, head[0]&0x0F, data
    except (socket.timeout, OSError):
        return None
def connect(i):
    s=socket.create_connection((HOST,PORT),timeout=5)
    vh=enc_str("MQTT")+bytes([0x04])+bytes([0xC2])+struct.pack(">H",25)
    pl=enc_str("UWPClient_%d"%i)+enc_str("UWPClient_User_%d"%i)+enc_str("UWPClient_Pwd888881772688_%d"%i)
    s.sendall(bytes([0x10])+enc_len(len(vh)+len(pl))+vh+pl)
    r=read_packet(s,5)
    if not r or r[0]!=2 or r[2][1]!=0: s.close(); return None
    return s

s=None
for i in range(1,10):
    s=connect(i)
    if s: print("clientID=UWPClient_%d"%i, flush=True); break
if not s: sys.exit("无空闲 clientID")
for t in TOPICS:
    sub=struct.pack(">H",1)+enc_str(t)+bytes([0])
    s.sendall(bytes([0x82])+enc_len(len(sub))+sub)
read_packet(s,3)
print("已订阅 %d 个主题，观察 16 秒…\n"%len(TOPICS), flush=True)
got={}
deadline=time.time()+16
while time.time()<deadline:
    r=read_packet(s,max(0.05,deadline-time.time()))
    if not r or r[0]!=3: continue
    d=r[2]; tl=struct.unpack(">H",d[:2])[0]
    got[d[2:2+tl].decode("utf-8","replace")]=d[2+tl:]
s.close()
print("="*74); print("真正会推数据的主题"); print("="*74)
for t in sorted(got):
    print("\n---- [%s]"%t)
    try:
        obj=json.loads(got[t].decode("utf-8"))
        if isinstance(obj,dict):
            for k,v in obj.items(): print("    %-22s %r"%(k,v))
        else: print("   ",obj)
    except Exception:
        print("   ", got[t].decode("utf-8","replace")[:400])
missing=[t for t in TOPICS if t not in got]
print("\n订阅了但一条都没推的: %s"%missing)
