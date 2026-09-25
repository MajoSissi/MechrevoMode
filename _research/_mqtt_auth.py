import sys, time
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688

CANDIDATES = [
    dict(client_id='UWPClient_3'),
    dict(client_id='UWPClient_3', username='UWPClient_User_4', password='UWPClient_Pwd888881772688_4'),
    dict(client_id='UWPClient_User_4', username='UWPClient_User_4', password='UWPClient_Pwd888881772688_4'),
    dict(client_id='UWPClient_User_4'),
    dict(client_id='UWPClient', username='UWPClient_User_4', password='UWPClient_Pwd888881772688_4'),
    dict(client_id='UWPSTDClient'),
    dict(client_id='UWPIntelClient'),
    dict(client_id='UWPADATAClient'),
]

results = {}

def run(idx, opts, wait=3.0):
    label = f"#{idx} cid={opts.get('client_id')} user={opts.get('username')}"
    msgs = []
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311, **opts)

    def on_connect(client, userdata, flags, rc, properties=None):
        print(f'  [{label}] on_connect rc={rc}')
        if str(rc) in ('Success', '0'):
            client.subscribe('#', qos=0)
            print(f'  [{label}] SUBSCRIBED #')

    def on_message(client, userdata, msg):
        msgs.append((msg.topic, msg.payload))
        try:
            p = msg.payload.decode('utf-8', 'replace')
        except Exception:
            p = repr(msg.payload)
        print(f'  [{label}] MSG {msg.topic!r} -> {p[:400]!r}')

    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(HOST, PORT, 30)
    except Exception as e:
        print(f'  [{label}] connect error {e}')
        return
    c.loop_start()
    time.sleep(wait)
    print(f'  [{label}] total msgs={len(msgs)}')
    c.loop_stop()
    try:
        c.disconnect()
    except Exception:
        pass
    results[label] = len(msgs)

for i, o in enumerate(CANDIDATES, 1):
    print('=' * 70)
    run(i, o)

print('=' * 70)
print('SUMMARY')
for k, v in results.items():
    print(f'  {k} -> {v} msgs')
