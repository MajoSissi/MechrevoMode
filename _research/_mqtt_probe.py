import sys, time, threading
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688

CLIENT_IDS = sys.argv[1].split(',') if len(sys.argv) > 1 else [
    'MyControlCenterUser', 'OcToolUser', 'probe-py', 'MyTrayClient'
]

def make(cid):
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, protocol=mqtt.MQTTv311)
    except Exception:
        return mqtt.Client(client_id=cid, protocol=mqtt.MQTTv311)

for cid in CLIENT_IDS:
    print('=' * 60)
    print('TRY client_id =', cid)
    c = make(cid)
    got = []

    def on_connect(client, userdata, flags, reason_code, properties=None):
        print('  on_connect rc =', reason_code, 'flags =', flags)
        client.subscribe('#', qos=0)
        print('  subscribed to #')

    def on_message(client, userdata, msg):
        got.append(msg)
        try:
            p = msg.payload.decode('utf-8', 'replace')
        except Exception:
            p = repr(msg.payload)
        print(f'  MSG topic={msg.topic!r} len={len(msg.payload)} payload={p[:300]!r}')

    def on_disconnect(client, userdata, rc, *a):
        print('  on_disconnect', rc)

    c.on_connect = on_connect
    c.on_message = on_message
    c.on_disconnect = on_disconnect
    try:
        c.connect(HOST, PORT, 30)
    except Exception as e:
        print('  CONNECT FAILED:', e)
        continue
    c.loop_start()
    time.sleep(4)
    c.loop_stop()
    c.disconnect()
    print(f'  -> messages received: {len(got)}')
