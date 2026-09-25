import time, threading
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688

def mk(n):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{n}', protocol=mqtt.MQTTv311)
    c.username_pw_set(f'UWPClient_User_{n}', f'UWPClient_Pwd888881772688_{n}')
    ev = {'conn': 0, 'disc': 0, 'last': None}
    c.on_connect = lambda cl, u, f, rc, p=None: ev.update(conn=ev['conn'] + 1, last=f'connect rc={rc}')
    c.on_disconnect = lambda cl, u, rc, *a: ev.update(disc=ev['disc'] + 1, last=f'disconnect rc={rc}')
    return c, ev

print('== 同时用两个相同的 client_id (UWPClient_0) ==')
a, ea = mk(0)
b, eb = mk(0)
a.connect(HOST, PORT, 20); a.loop_start()
time.sleep(1.5)
b.connect(HOST, PORT, 20); b.loop_start()
time.sleep(10)
print(f'  A: connects={ea["conn"]} disconnects={ea["disc"]} last={ea["last"]}')
print(f'  B: connects={eb["conn"]} disconnects={eb["disc"]} last={eb["last"]}')
a.loop_stop(); a.disconnect()
b.loop_stop(); b.disconnect()
time.sleep(1)

print()
print('== 逐个测试各 index 的稳定性 (每个 6 秒) ==')
for n in range(0, 10):
    c, ev = mk(n)
    try:
        c.connect(HOST, PORT, 20)
    except Exception as e:
        print(f'  idx {n}: connect exception {e}')
        continue
    c.loop_start()
    time.sleep(6)
    c.loop_stop()
    try: c.disconnect()
    except Exception: pass
    print(f'  idx {n}: connects={ev["conn"]} disconnects={ev["disc"]}  last={ev["last"]}')
    time.sleep(0.6)
