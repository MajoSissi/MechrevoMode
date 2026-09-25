import time, sys, json, datetime
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 25

N = None
for candidate in range(0, 10):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{candidate}',
                    protocol=mqtt.MQTTv311)
    c.username_pw_set(f'UWPClient_User_{candidate}', f'UWPClient_Pwd888881772688_{candidate}')
    got = {}
    c.on_connect = lambda cl, u, f, rc, p=None: got.setdefault('rc', rc)
    try:
        c.connect(HOST, PORT, 10)
    except Exception:
        continue
    c.loop_start()
    time.sleep(0.7)
    c.loop_stop()
    rc = got.get('rc')
    try:
        c.disconnect()
    except Exception:
        pass
    if str(rc) in ('0', 'Success'):
        N = candidate
        print(f'>>> VALID SESSION INDEX N = {candidate}')
        break
    else:
        print(f'  N={candidate} rc={rc}')

if N is None:
    print('no valid N found')
    sys.exit(1)

topics = {}
events = []

def on_connect(client, userdata, flags, rc, properties=None):
    print('connected rc =', rc)
    client.subscribe('#', qos=0)
    print('subscribed #')

def on_message(client, userdata, msg):
    ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
    try:
        p = msg.payload.decode('utf-8')
    except Exception:
        p = repr(msg.payload)
    topics[msg.topic] = topics.get(msg.topic, 0) + 1
    events.append((ts, msg.topic, p))
    print(f'{ts} | {msg.topic} | {p[:500]}')

def on_disconnect(client, userdata, rc, *a):
    print('!!! DISCONNECTED', rc, datetime.datetime.now().strftime('%H:%M:%S'))

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{N}', protocol=mqtt.MQTTv311)
c.username_pw_set(f'UWPClient_User_{N}', f'UWPClient_Pwd888881772688_{N}')
c.on_connect = on_connect
c.on_message = on_message
c.on_disconnect = on_disconnect
c.connect(HOST, PORT, 30)
c.loop_start()
print(f'listening {DUR}s ...')
time.sleep(DUR)
c.loop_stop()
c.disconnect()

print()
print('===== TOPIC SUMMARY =====')
for t, n in sorted(topics.items(), key=lambda x: -x[1]):
    print(f'{n:6d}  {t}')
