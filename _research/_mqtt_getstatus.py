import time, json, sys
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
N = 0

msgs = []

def on_connect(client, userdata, flags, rc, properties=None):
    print('connected', rc)
    client.subscribe('#', qos=0)

def on_message(client, userdata, msg):
    try:
        p = msg.payload.decode('utf-8')
    except Exception:
        p = repr(msg.payload)
    msgs.append((msg.topic, p))
    print(f'  << {msg.topic} | {p[:400]}')

c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{N}', protocol=mqtt.MQTTv311)
c.username_pw_set(f'UWPClient_User_{N}', f'UWPClient_Pwd888881772688_{N}')
c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 30)
c.loop_start()
time.sleep(1.5)

TESTS = [
    ('Fan/Control', '{"Action":"GETSTATUS"}'),
    ('Customize/SupportControl', '{"Action":"GETSUPPORT"}'),
    ('Fan/Control', '{ Action = GETSTATUS }'),
]

for topic, payload in TESTS:
    print(f'--- PUBLISH {topic} <- {payload!r}')
    before = len(msgs)
    c.publish(topic, payload, qos=0)
    time.sleep(3)
    print(f'    responses: {len(msgs) - before}')

time.sleep(2)
c.loop_stop()
c.disconnect()

print()
print('=== all topics seen ===')
seen = {}
for t, p in msgs:
    seen.setdefault(t, p)
for t, p in seen.items():
    print(f'{t} | {p[:300]}')
