# 验证「按需触发」：向 System/Control 发动作，能不能让它立刻推一条 CpuInfo/FanInfo/GpuInfo。
# 意义：订阅推送是 2 秒一次；如果能按需拉，我们就能做成每秒刷新（或只在提示要更新时拉）。
import json, socket, struct, sys, time
HOST, PORT = "127.0.0.1", 13688
WATCH = {b"System/CpuInfo", b"System/GpuInfo", b"System/FanInfo"}

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

def publish(s,topic,body):
    pv=enc_str(topic); s.sendall(bytes([0x30])+enc_len(len(pv)+len(body))+pv+body)

def pump(s, seconds, seen):
    deadline=time.time()+seconds
    while time.time()<deadline:
        r=read_packet(s,max(0.02,deadline-time.time()))
        if not r or r[0]!=3: continue
        d=r[2]; tl=struct.unpack(">H",d[:2])[0]
        seen.append((time.time(), d[2:2+tl], d[2+tl:]))

s=None
for i in range(1,10):
    s=connect(i)
    if s: print("clientID=UWPClient_%d"%i, flush=True); break
if not s: sys.exit("无空闲 clientID")

for t in WATCH:
    sub=struct.pack(">H",1)+enc_str(t.decode())+bytes([0])
    s.sendall(bytes([0x82])+enc_len(len(sub))+sub)
read_packet(s,3)

print("\n先空跑 6 秒，确认自发推送的节奏：", flush=True)
seen=[]; pump(s,6.0,seen)
for ts,topic,_ in seen: print("   t=%.3f  %s"%(ts, topic.decode()), flush=True)
sink=[ts for ts,_,_ in seen]

print("\n现在逐个发动作，看能否立刻触发推送（记录距上次推送的间隔）：", flush=True)
for act in ["GETSTATUS","GETCPUINFO","GETFANINFO","GETGPUINFO","GETINFO"]:
    seen=[]; pump(s,1.2,seen)          # 先静置，清掉在途消息
    t0=time.time()
    publish(s,"System/Control", json.dumps({"Action":act}).encode())
    got=[]; pump(s,1.2,got)
    if got:
        for ts,topic,_ in got:
            print("   %-12s -> +%5.0f ms  %s" % (act, (ts-t0)*1000, topic.decode()), flush=True)
    else:
        print("   %-12s -> 1.2 秒内没有任何推送" % act, flush=True)
s.close()
