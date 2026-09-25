"""验证「不带 ProfileIndex 的狂暴切换」是否有效"""
import time, json
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
N = 6

latest = {}


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe('Fan/Status', qos=0)


def on_message(client, userdata, msg):
    if msg.topic == 'Fan/Status':
        try:
            latest.update(json.loads(msg.payload.decode('utf-8')))
        except Exception:
            pass


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'UWPClient_{N}', protocol=mqtt.MQTTv311)
c.username_pw_set(f'UWPClient_User_{N}', f'UWPClient_Pwd888881772688_{N}')
c.on_connect = on_connect
c.on_message = on_message
c.connect(HOST, PORT, 30)
c.loop_start()
time.sleep(1.5)


def snap(tag):
    c.publish('Fan/Control', '{"Action":"GETSTATUS"}', qos=0)
    time.sleep(1.2)
    print(f'--- [{tag}]')
    print('    ' + json.dumps({k: latest.get(k) for k in (
        'OperatingMode', 'ProfileName', 'FAN_TableName', 'TurboProfileIndex',
        'CPU_PL1', 'CPU_PL2', 'GPU_ConfigurableTGPTarget')}, ensure_ascii=False))


def send(tag, payload):
    print(f'>>> {tag}  {payload}')
    c.publish('Fan/Control', payload, qos=0)
    time.sleep(2.6)
    snap(tag)


# 先落到自定义档位，改变「上次狂暴档位」的上下文
c.publish('Fan/Control', '{"Action":"OPERATING_CUSTOM_MODE","ProfileIndex":2}', qos=0)
time.sleep(2.5)

# 先把狂暴档位设成 1（静音），再用不带 ProfileIndex 的狂暴命令，看是沿用 1 还是变 0
send('A: 先设狂暴档位=1', '{"Action":"OPERATING_TURBO_MODE","ProfileIndex":1}')
send('B: 回自定义', '{"Action":"OPERATING_CUSTOM_MODE","ProfileIndex":2}')
send('C: 不带 ProfileIndex 切狂暴', '{"Action":"OPERATING_TURBO_MODE"}')
send('D: 再设狂暴档位=0', '{"Action":"OPERATING_TURBO_MODE","ProfileIndex":0}')
send('E: 回自定义', '{"Action":"OPERATING_CUSTOM_MODE","ProfileIndex":2}')
send('F: 不带 ProfileIndex 切狂暴', '{"Action":"OPERATING_TURBO_MODE"}')

# 还原到自定义 1
c.publish('Fan/Control', '{"Action":"OPERATING_CUSTOM_MODE","ProfileIndex":0}', qos=0)
time.sleep(2.5)
snap('restored')

c.loop_stop()
c.disconnect()
