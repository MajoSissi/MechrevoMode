import time, sys
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
HOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0

results = []
for n in range(0, 10):
    ev = {'conn': 0, 'disc': 0, 'self': False}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{n}', protocol=mqtt.MQTTv311)
    c.username_pw_set(f'UWPClient_User_{n}', f'UWPClient_Pwd888881772688_{n}')
    c.on_connect = lambda cl, u, f, rc, p=None: ev.update(conn=ev['conn'] + 1)
    c.on_disconnect = lambda cl, u, rc, *a: (None if ev['self'] else ev.update(disc=ev['disc'] + 1))
    try:
        c.connect(HOST, PORT, 20)
    except Exception as e:
        print(f'idx {n}: connect exception {e}', flush=True)
        continue
    c.loop_start()
    time.sleep(HOLD)
    ev['self'] = True
    c.loop_stop()
    try: c.disconnect()
    except Exception: pass
    verdict = 'CONFLICT (被官方客户端顶掉)' if ev['disc'] > 0 or ev['conn'] > 1 else 'FREE / stable'
    results.append((n, ev['conn'], ev['disc'], verdict))
    print(f'idx {n}: connects={ev["conn"]} unexpected_disconnects={ev["disc"]}  -> {verdict}', flush=True)
    time.sleep(1.0)

print()
print('=== SUMMARY ===')
for n, cc, dd, v in results:
    print(f'  idx {n}: {v}')
