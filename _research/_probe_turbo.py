"""狂暴子模式（静音狂暴 / 高能狂暴）命令探测
依次下发候选 payload，观察 Fan/Status 里哪些字段在变。
"""
import time, json
import paho.mqtt.client as mqtt

HOST, PORT = '127.0.0.1', 13688
N = 6

latest = {}
lock_msgs = []

KEYS = [
    'OperatingMode', 'ProfileName', 'FAN_TableName',
    'OfficeProfileIndex', 'GamingProfileIndex', 'TurboProfileIndex', 'CustomProfileIndex',
    'CPU_PL1', 'CPU_PL2', 'CPU_PL4',
    'CPU_AmdSPL', 'CPU_AmdSPPT', 'CPU_AmdFPPT', 'CPU_AmdTccTarget',
    'GPU_ConfigurableTGPTarget', 'GPU_DynamicBoost', 'GPU_CoreClockOffset', 'GPU_MemoryClockOffset',
    'FanBoostEnable', 'FAN_FanSwitchSpeed', 'OverClockingSwitch',
]


def on_connect(client, userdata, flags, rc, properties=None):
    client.subscribe('Fan/Status', qos=0)
    client.subscribe('Fan/Control', qos=0)


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
    row = {k: latest.get(k) for k in KEYS}
    print(f'--- [{tag}]')
    print('    ' + json.dumps(row, ensure_ascii=False))
    return row


def send(tag, payload):
    print(f'>>> {tag}  {payload}')
    c.publish('Fan/Control', payload, qos=0)
    time.sleep(2.6)
    return snap(tag)


snap('baseline(初始)')

send('T0: TURBO ProfileIndex=0', '{"Action":"OPERATING_TURBO_MODE","ProfileIndex":0}')
send('T1: TURBO ProfileIndex=1', '{"Action":"OPERATING_TURBO_MODE","ProfileIndex":1}')
send('T2: TURBO ProfileIndex=2', '{"Action":"OPERATING_TURBO_MODE","ProfileIndex":2}')

send('S0: SET_CPU_CORE_OFFSET_SILENT', '{"Action":"SET_CPU_CORE_OFFSET_SILENT"}')
send('S1: SET_CPU_CORE_OFFSET_EXTREME', '{"Action":"SET_CPU_CORE_OFFSET_EXTREME"}')

send('V0: TURBO+Silent=1', '{"Action":"OPERATING_TURBO_MODE","ProfileIndex":1,"Silent":"1"}')
send('V1: SwitchTurboSubMode', '{"Action":"SwitchTurboSubMode","Silent":"1"}')

print()
print('>>> 还原到 自定义1')
c.publish('Fan/Control', '{"Action":"OPERATING_CUSTOM_MODE","ProfileIndex":0}', qos=0)
time.sleep(2.5)
snap('restored')

time.sleep(0.5)
c.loop_stop()
c.disconnect()
