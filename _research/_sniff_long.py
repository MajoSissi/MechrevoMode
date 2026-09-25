import time, sys, datetime, json
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 600
LOGF = r"D:\User\Desktop\MechrevoMode\_sniff.log"

N = None
for cand in range(0, 10):
    cc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{cand}',
                     protocol=mqtt.MQTTv311)
    cc.username_pw_set(f'UWPClient_User_{cand}', f'UWPClient_Pwd888881772688_{cand}')
    res = {}
    cc.on_connect = lambda cl, u, f, rc, p=None: res.setdefault('rc', rc)
    try:
        cc.connect(HOST, PORT, 10)
    except Exception:
        continue
    cc.loop_start(); time.sleep(0.6); cc.loop_stop()
    try: cc.disconnect()
    except Exception: pass
    if str(res.get('rc')) in ('0', 'Success'):
        N = cand
        break

if N is None:
    print('NO VALID CLIENT INDEX'); sys.exit(1)

print(f'SNIFFER READY (client index {N}), logging to {LOGF}', flush=True)

fh = open(LOGF, 'a', encoding='utf-8')
fh.write(f'\n===== sniffer session {datetime.datetime.now()} =====\n')
fh.flush()

def log(line):
    print(line, flush=True)
    fh.write(line + '\n')
    fh.flush()

def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe('#', qos=0)
    log(f'[connected rc={rc}] subscribed #')

def on_message(client, userdata, msg):
    ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
    try:
        p = msg.payload.decode('utf-8')
    except Exception:
        p = repr(msg.payload)
    if len(p) > 1200:
        p = p[:1200] + '...<truncated>'
    log(f'{ts} | {msg.topic} | {p}')

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{N}', protocol=mqtt.MQTTv311)
c.username_pw_set(f'UWPClient_User_{N}', f'UWPClient_Pwd888881772688_{N}')
c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 60)
c.loop_start()

# periodic status poll so we can see OperatingMode transitions
t0 = time.time()
while time.time() - t0 < DUR:
    time.sleep(3)
    try:
        c.publish('Fan/Control', '{"Action":"GETSTATUS"}', qos=0)
    except Exception:
        pass

c.loop_stop()
try: c.disconnect()
except Exception: pass
fh.close()
print('sniffer done')
