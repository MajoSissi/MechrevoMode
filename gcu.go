//go:build windows

package main

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"runtime/debug"
	"strconv"
	"sync"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

// GCU 与机械革命 GCU 后台服务通信的客户端
//
// 官方控制台的通信方式是本机 MQTT（GCUBridge 内嵌 broker，TCP 13688）：
//
//	clientId = UWPClient_<n>
//	username = UWPClient_User_<n>
//	password = UWPClient_Pwd888881772688_<n>
//
// 订阅 Fan/Status、Tray/Status 获取状态，向 Fan/Control 下发命令。
const (
	gcuHost       = "127.0.0.1"
	gcuPort       = 13688
	topicFanCtrl  = "Fan/Control"
	topicFanStat  = "Fan/Status"
	topicTrayStat = "Tray/Status"

	mqtt311 = 4

	customSlotCount = 5
)

// 模式编号与官方枚举一致：0 办公 / 1 均衡 / 2 狂暴 / 3 自定义
const (
	ModeOffice  = 0
	ModeBalance = 1
	ModeTurbo   = 2
	ModeCustom  = 3
)

func actionForMode(mode int) string {
	switch mode {
	case ModeOffice:
		return "OPERATING_OFFICE_MODE"
	case ModeBalance:
		return "OPERATING_GAMING_MODE"
	case ModeTurbo:
		return "OPERATING_TURBO_MODE"
	case ModeCustom:
		return "OPERATING_CUSTOM_MODE"
	}
	return ""
}

// StateChange 状态变化回调（在 MQTT 线程调用，需自行投递到 UI 线程）
type StateChange func(mode, profile int, online bool)

type GCU struct {
	mu      sync.Mutex
	client  mqtt.Client
	index   int
	online  bool
	mode    int
	profile int

	// 命令下发后的「落定期」：GCU 切换过程中会先回几条旧状态。
	// 期间只接受与目标 mode+slot 完全一致的回报，否则档位会被冲回上一个值
	// （狂暴的高能/静音两个档位就是这样互相覆盖的）。
	pendMode  int
	pendSlot  int
	pendUntil time.Time

	// 最近一次 Fan/Status 里的原始档位名，仅用于日志与排查
	profName string

	// 探测到的可用客户端序号，-1 表示无新发现（由 UI 线程取走并落盘）
	discIdx int
	// 首选客户端序号（启动时从配置读入一次）
	prefer int

	onChange StateChange
	logFn    func(string, ...interface{})

	lostCh  chan struct{}
	stopCh  chan struct{}
	stopped bool

	badIndex map[int]int // index -> 失败次数
}

func NewGCU(prefer int, onChange StateChange) *GCU {
	return &GCU{
		prefer:   prefer,
		onChange: onChange,
		lostCh:   make(chan struct{}, 8),
		stopCh:   make(chan struct{}),
		badIndex: map[int]int{},
		mode:     -1,
		profile:  -1,
		index:    -1,
		discIdx:  -1,
	}
}

// SetLogger 注入日志函数（可选）
func (g *GCU) SetLogger(fn func(string, ...interface{})) { g.logFn = fn }

func (g *GCU) logf(format string, args ...interface{}) {
	if g.logFn != nil {
		g.logFn(format, args...)
	}
}

// guard 用于 defer，捕获后台 goroutine 里的 panic。
// 未捕获的 panic 会直接终结整个进程，而 GUI 程序看不到任何输出。
func (g *GCU) guard(where string) {
	if r := recover(); r != nil {
		g.logf("!! PANIC @%s: %v\r\n%s", where, r, debug.Stack())
	}
}

func (g *GCU) Snapshot() (mode, profile int, online bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.mode, g.profile, g.online
}

// Settling 是否处于「命令落定期」。落定期内状态回报还不可信，
// 调用方不应据此改写「当前档位」这种持久化状态。
func (g *GCU) Settling() bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	return !g.pendUntil.IsZero() && time.Now().Before(g.pendUntil)
}

// maxSlotFor 各模式支持的档位上限。
// 实测：给狂暴模式下发越界的 ProfileIndex 会让 GCU 切到完全不相干的模式
// （ProfileIndex=2 直接跳到了「均衡」），所以必须在本地拦住。
func maxSlotFor(mode int) int {
	switch mode {
	case ModeTurbo:
		return 1 // 0 = 高能狂暴，1 = 静音狂暴
	case ModeCustom:
		return customSlotCount - 1
	}
	return 0
}

// TakeDiscoveredIndex 取走本次探测到的客户端序号（返回 -1 表示无）
func (g *GCU) TakeDiscoveredIndex() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	v := g.discIdx
	g.discIdx = -1
	return v
}

func (g *GCU) notify() {
	if g.onChange == nil {
		return
	}
	g.mu.Lock()
	m, p, o := g.mode, g.profile, g.online
	g.mu.Unlock()
	g.onChange(m, p, o)
}

// Start 启动后台连接管理
func (g *GCU) Start() { go g.supervise() }

func (g *GCU) Stop() {
	g.mu.Lock()
	if g.stopped {
		g.mu.Unlock()
		return
	}
	g.stopped = true
	c := g.client
	g.mu.Unlock()

	close(g.stopCh)
	if c != nil && c.IsConnected() {
		c.Disconnect(120)
	}
}

// ---------------------------------------------------------------- 连接管理

func (g *GCU) setOnline(v bool) {
	g.mu.Lock()
	changed := g.online != v
	g.online = v
	g.mu.Unlock()
	if changed {
		g.notify()
	}
}

func (g *GCU) blacklist(idx int) { g.badIndex[idx]++ }

// candidateIndexes 返回尝试顺序
func (g *GCU) candidateIndexes(prefer int) []int {
	seen := map[int]bool{}
	out := make([]int, 0, 12)
	if prefer >= 0 && prefer <= 9 {
		out = append(out, prefer)
		seen[prefer] = true
	}
	for _, n := range rand.Perm(10) {
		if seen[n] {
			continue
		}
		out = append(out, n)
		seen[n] = true
	}
	return out
}

func (g *GCU) options(idx int) *mqtt.ClientOptions {
	opts := mqtt.NewClientOptions()
	opts.AddBroker(fmt.Sprintf("tcp://%s:%d", gcuHost, gcuPort))
	opts.SetClientID(fmt.Sprintf("UWPClient_%d", idx))
	opts.SetUsername(fmt.Sprintf("UWPClient_User_%d", idx))
	opts.SetPassword(fmt.Sprintf("UWPClient_Pwd888881772688_%d", idx))
	opts.SetProtocolVersion(mqtt311)
	opts.SetCleanSession(true)
	opts.SetConnectTimeout(3 * time.Second)
	opts.SetKeepAlive(25 * time.Second)
	opts.SetPingTimeout(10 * time.Second)
	opts.SetAutoReconnect(false)
	opts.SetConnectRetry(false)
	opts.SetOrderMatters(true)

	opts.SetOnConnectHandler(func(c mqtt.Client) {
		defer g.guard("onConnect")
		g.onConnected(c)
	})
	opts.SetConnectionLostHandler(func(c mqtt.Client, err error) {
		defer g.guard("onConnectionLost")
		g.logf("GCU 连接断开: %v", err)
		g.setOnline(false)
		select {
		case g.lostCh <- struct{}{}:
		default:
		}
	})
	return opts
}

func (g *GCU) onConnected(c mqtt.Client) {
	g.setOnline(true)
	c.Subscribe(topicFanStat, 0, func(_ mqtt.Client, m mqtt.Message) {
		defer g.guard("handleFanStatus")
		g.handleFanStatus(m.Payload())
	})
	c.Subscribe(topicTrayStat, 0, func(_ mqtt.Client, m mqtt.Message) {
		defer g.guard("handleTrayStatus")
		g.handleTrayStatus(m.Payload())
	})
	c.Publish(topicFanCtrl, 0, false, `{"Action":"GETSTATUS"}`)
}

// supervise 维护连接：探测可用的客户端序号、断线重连、周期性同步
func (g *GCU) supervise() {
	defer g.guard("supervise")

	prefer := g.prefer
	failLogged := false

	for {
		select {
		case <-g.stopCh:
			return
		default:
		}

		var client mqtt.Client
		var idx int
		ok := false
		for _, cand := range g.candidateIndexes(prefer) {
			if g.badIndex[cand] >= 3 {
				continue
			}
			c := mqtt.NewClient(g.options(cand))
			tok := c.Connect()
			if !tok.WaitTimeout(4*time.Second) || tok.Error() != nil {
				c.Disconnect(0)
				g.blacklist(cand)
				continue
			}
			client, idx, ok = c, cand, true
			break
		}
		if !ok {
			if !failLogged {
				failLogged = true
				g.logf("无法连接 GCU 服务（127.0.0.1:13688），将自动重试")
			}
			time.Sleep(5 * time.Second)
			continue
		}
		failLogged = false

		g.mu.Lock()
		g.client = client
		g.index = idx
		g.discIdx = idx
		g.mu.Unlock()
		g.logf("已连接 GCU，client index=%d", idx)
		prefer = idx
		g.notify() // 让 UI 线程取走序号并落盘

		connectedAt := time.Now()
		fastDrops := 0

	wait:
		for {
			select {
			case <-g.stopCh:
				client.Disconnect(80)
				return
			case <-g.lostCh:
				if time.Since(connectedAt) < 5*time.Second {
					fastDrops++
				} else {
					fastDrops = 0
				}
				if fastDrops >= 3 {
					// 该序号很可能被官方托盘占用，换一个
					g.blacklist(idx)
					prefer = -1
				}
				break wait
			case <-time.After(20 * time.Second):
				if client.IsConnected() {
					client.Publish(topicFanCtrl, 0, false, `{"Action":"GETSTATUS"}`)
				} else {
					g.logf("周期检查：连接已失效")
					break wait
				}
			}
		}
		client.Disconnect(60)
		g.setOnline(false)
		time.Sleep(1500 * time.Millisecond)
	}
}

// ---------------------------------------------------------------- 状态解析

type fanStatusPayload struct {
	OperatingMode      string `json:"OperatingMode"`
	ProfileName        string `json:"ProfileName"`
	FanTableName       string `json:"FAN_TableName"`
	GamingProfileIndex string `json:"GamingProfileIndex"`
	OfficeProfileIndex string `json:"OfficeProfileIndex"`
	TurboProfileIndex  string `json:"TurboProfileIndex"`
	CustomProfileIndex string `json:"CustomProfileIndex"`
}

func atoiOr(s string, def int) int {
	if s == "" {
		return def
	}
	n, err := strconv.Atoi(s)
	if err != nil {
		return def
	}
	return n
}

// applyStatus 只更新内部状态并回调，不碰配置（配置写入统一留在 UI 线程）
//
// profName 是 GCU 报文里的原始档位名（如 Mode3_Profile2），仅用于日志。
func (g *GCU) applyStatus(mode int, profile int, profName string) {
	if mode < 0 || mode > 3 {
		return
	}
	g.mu.Lock()

	confirmed := false
	if !g.pendUntil.IsZero() {
		if time.Now().Before(g.pendUntil) {
			// 落定期内：模式必须一致；目标带档位时，档位也必须一致。
			// 只对一半的回报照单全收，就会把刚切好的档位冲回上一个值。
			if mode != g.pendMode || (g.pendSlot >= 0 && profile != g.pendSlot) {
				g.mu.Unlock()
				return
			}
			confirmed = true
		}
		// 匹配成功或已超时，落定期结束
		g.pendUntil = time.Time{}
		g.pendSlot = -1
	}

	if profName != "" {
		g.profName = profName
	}
	changed := g.mode != mode || g.profile != profile || !g.online
	g.mode = mode
	g.profile = profile
	g.online = true
	g.mu.Unlock()

	if confirmed {
		g.logf("档位生效 -> %s (mode=%d profile=%d)", profName, mode, profile)
	}
	if changed {
		g.logf("状态更新 -> mode=%d profile=%d", mode, profile)
		g.notify()
	}
}

// ApplyLocal 命令刚发出时先本地反映状态，让托盘图标立刻变化
func (g *GCU) ApplyLocal(mode, profile int) {
	if mode < 0 || mode > 3 {
		return
	}
	g.mu.Lock()
	changed := g.mode != mode || g.profile != profile
	g.mode = mode
	g.profile = profile
	g.mu.Unlock()
	if changed {
		g.notify()
	}
}

func (g *GCU) handleTrayStatus(payload []byte) {
	var p fanStatusPayload
	if err := json.Unmarshal(payload, &p); err != nil {
		return
	}
	mode := atoiOr(p.OperatingMode, -1)
	if mode < 0 {
		return
	}
	// Tray/Status 只带模式、不带档位。切换命令刚下发时它会推来一条旧报文，
	// 此时若沿用当前档位回报，就会把刚切好的档位冲回上一个值——这正是
	// 「两个狂暴档位互相覆盖」的来源，所以落定期内直接丢弃。
	if g.Settling() {
		return
	}
	g.mu.Lock()
	curMode := g.mode
	profile := g.profile
	g.mu.Unlock()
	// 模式与当前一致时该报文不含任何新信息，不要动档位
	if mode == curMode {
		return
	}
	g.applyStatus(mode, profile, "")
}

func (g *GCU) handleFanStatus(payload []byte) {
	var p fanStatusPayload
	if err := json.Unmarshal(payload, &p); err != nil {
		return
	}
	mode := atoiOr(p.OperatingMode, -1)
	if mode < 0 {
		return
	}
	var profile int
	switch mode {
	case ModeOffice:
		profile = atoiOr(p.OfficeProfileIndex, 0)
	case ModeBalance:
		profile = atoiOr(p.GamingProfileIndex, 0)
	case ModeTurbo:
		profile = atoiOr(p.TurboProfileIndex, 0)
	case ModeCustom:
		profile = atoiOr(p.CustomProfileIndex, 0)
	}
	g.applyStatus(mode, profile, p.ProfileName)
}

// ---------------------------------------------------------------- 下发命令

// SetMode 切换模式。slot < 0 表示沿用该模式自己的档位。
func (g *GCU) SetMode(mode, slot int) error {
	if actionForMode(mode) == "" {
		return fmt.Errorf("未知模式 %d", mode)
	}
	// 档位越界会让 GCU 切到别的模式，本地先拦一道
	if slot >= 0 && (mode == ModeTurbo || mode == ModeCustom) {
		if max := maxSlotFor(mode); slot > max {
			return fmt.Errorf("档位 %d 超出范围（该模式仅支持 0..%d）", slot, max)
		}
	}

	g.mu.Lock()
	c := g.client
	online := g.online
	g.mu.Unlock()

	if c == nil || !online || !c.IsConnected() {
		return fmt.Errorf("GCU 服务未连接")
	}

	var payload string
	if slot < 0 {
		payload = fmt.Sprintf(`{"Action":"%s"}`, actionForMode(mode))
	} else {
		payload = fmt.Sprintf(`{"Action":"%s","ProfileIndex":%d}`, actionForMode(mode), slot)
	}

	// 必须先进入落定期再下发。反过来写的话，GCU 在 publish 返回前推来的
	// 旧状态会被当成有效回报，档位随即被冲回上一个值。
	g.mu.Lock()
	g.pendMode = mode
	g.pendSlot = slot
	g.pendUntil = time.Now().Add(2 * time.Second)
	g.mu.Unlock()

	tok := c.Publish(topicFanCtrl, 0, false, payload)
	ok := tok.WaitTimeout(3 * time.Second)
	if !ok || tok.Error() != nil {
		g.mu.Lock()
		g.pendUntil = time.Time{}
		g.pendSlot = -1
		g.mu.Unlock()
		if !ok {
			return fmt.Errorf("下发超时")
		}
		return tok.Error()
	}
	g.logf("下发 %s", payload)
	return nil
}

// Refresh 主动拉取一次状态
func (g *GCU) Refresh() {
	g.mu.Lock()
	c := g.client
	g.mu.Unlock()
	if c != nil && c.IsConnected() {
		c.Publish(topicFanCtrl, 0, false, `{"Action":"GETSTATUS"}`)
	}
}
