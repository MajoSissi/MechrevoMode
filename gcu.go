//go:build windows

package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"runtime/debug"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

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
//
// 后端是**两份**组件，缺一不可（本机实测）：
//
//	GCUBridge  —— Windows 服务，提供 broker，监听 13688
//	GCUService —— 普通进程，真正发布 Fan/Status 的那一个
//
// 而且 GCUBridge 开机时会自己异常终止（系统日志事件 7023），服务上又**没有**配置
// 恢复动作，于是端口无人监听、本工具永远连不上，直到用户手动打开一次官方控制台
// （控制台会把它重新拉起来）。详见 ensureBackend。
const (
	gcuHost       = "127.0.0.1"
	gcuPort       = 13688
	topicFanCtrl  = "Fan/Control"
	topicFanStat  = "Fan/Status"
	topicTrayStat = "Tray/Status"

	mqtt311 = 4

	customSlotCount = 5
)

// GCU 后端（broker 服务 + 状态发布进程）
const (
	gcuBridgeService = "GCUBridge"
	gcuPublisherExe  = "GCUService.exe"

	// 两次「拉起后端」尝试之间的最小间隔。
	// GCUService 起来之后实测要约 30 秒才开始发布状态，催得太急只会反复重启它。
	backendRetryInterval = 40 * time.Second

	// 我们自己拉起的发布者，等这么久还没带来任何状态就判它不行，换下一个候选
	publisherGrace = 90 * time.Second

	// 连上 broker 之后多久收不到任何状态，就认为发布者不在（GCU 后端没就绪）
	//
	// 取值要盖住实测的冷启动时间：GCUService 起来后约 30~45 秒才发第一条状态。
	// 另外检查是在下面的 20 秒周期里做的，所以真正生效的门限是「45 秒之后的第一个
	// 周期点」，也就是 60 秒 —— 正常冷启动（约 43 秒出状态）不会触发，一个误报都没有。
	brokerSilentGrace = 45 * time.Second
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

	badIndex map[int]int // index -> 失败次数。
	// 语义严格限定为「这个 clientID 被官方托盘占着」，
	// **不能**把「连不上」也算进来 —— 那会让序号耗尽后永不重试。

	// 最后一条状态报文的时刻。用来区分「连上了 broker」和「后端真的在发状态」：
	// broker 在、发布者不在时 CONNACK 一样返回 0，但一条状态都不会来。
	//
	// 刻意不用「收到过消息」这种布尔量：Fan/Status 是 retained 的，
	// 发布者早就死了也照样能收到一条陈旧的保留消息，看着像一切正常。
	// 而我们会每 20 秒发一次 GETSTATUS，活的发布者一定会回 —— 所以
	// 「多久没再听到任何动静」才是可靠判据。
	lastMsgAt time.Time

	// 后端自愈状态（见 ensureBackend）
	autoBackend func() bool
	backendAt   time.Time       // 上次尝试拉起后端的时间（节流用）
	pubPID      uint32          // 本程序自己拉起的发布者 pid，0 = 没拉起
	pubPath     string          // 上面那个发布者的路径
	pubAt       time.Time       // 拉起时刻（用来判断它到底有没有起作用）
	pubFailed   map[string]bool // 已判定不可用的发布者候选
}

func NewGCU(prefer int, onChange StateChange) *GCU {
	return &GCU{
		prefer:    prefer,
		onChange:  onChange,
		lostCh:    make(chan struct{}, 8),
		stopCh:    make(chan struct{}),
		badIndex:  map[int]int{},
		pubFailed: map[string]bool{},
		mode:      -1,
		profile:   -1,
		index:     -1,
		discIdx:   -1,
	}
}

// SetAutoBackendFn 注入「是否允许自动拉起 GCU 后端」的判断（读用户配置）
func (g *GCU) SetAutoBackendFn(fn func() bool) { g.autoBackend = fn }

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

// sleepOrStop 可被 Stop 打断的等待，免得退出时还要白白多等一整个周期
func sleepOrStop(stop <-chan struct{}, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-stop:
	case <-t.C:
	}
}

// gotStatusSince 自 t 之后是否又收到过状态报文
func (g *GCU) gotStatusSince(t time.Time) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.lastMsgAt.After(t)
}

// drainLost 丢掉断开通知队列里残留的条目。
// 刚连上时队列里若还剩着上一轮的断开通知，会立刻把新连接踢下去，形成重连风暴。
func drainLost(ch chan struct{}) {
	for {
		select {
		case <-ch:
		default:
			return
		}
	}
}

// supervise 维护连接：探测可用的客户端序号、断线重连、周期性同步
func (g *GCU) supervise() {
	defer g.guard("supervise")

	prefer := g.prefer
	fails := 0

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
				continue // 该序号被官方托盘占用，跳过
			}
			c := mqtt.NewClient(g.options(cand))
			tok := c.Connect()
			if !tok.WaitTimeout(4*time.Second) || tok.Error() != nil {
				c.Disconnect(0)
				// 这里**不能**计入 badIndex。
				//
				// badIndex 的含义是「这个 clientID 被别人占着」，而连不上
				// （端口根本没人监听）跟序号毫无关系。以前这里也调 blacklist，
				// 于是 GCU 后端没起来时 10 个序号会被一口气全部拉黑，
				// 之后每一轮都在循环开头被 continue 掉 —— 再也不重试。
				// 这就是「不先开一次控制台就永远连不上」的直接原因。
				continue
			}
			client, idx, ok = c, cand, true
			break
		}
		if !ok {
			fails++
			g.setOnline(false)
			// 首次 + 之后每 6 次（约 30 秒）报一次：既不刷屏，又能看出它一直在重试
			if fails == 1 || fails%6 == 1 {
				g.logf("无法连接 GCU 服务（%s:%d），第 %d 次失败，将自动重试", gcuHost, gcuPort, fails)
			}
			g.ensureBackend("broker 无响应")
			sleepOrStop(g.stopCh, 5*time.Second)
			continue
		}
		fails = 0
		drainLost(g.lostCh) // 新连接开始前清掉上一轮的断开通知

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
		silentLogged := false

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
				if !client.IsConnected() {
					g.logf("周期检查：连接已失效")
					break wait
				}
				// broker 通了、却一直等不到状态 —— 说明真正发布状态的进程不在。
				//
				// 判据必须从「本次连接的建立时刻」起算，不能算「距最后一条状态多久」：
				// lastMsgAt 的初值是零值时间，time.Since 一减就是天文数字，
				// 会导致每次刚连上就被误判成后端未就绪（实测多出两轮无谓的断开重连）。
				if !g.gotStatusSince(connectedAt) && time.Since(connectedAt) > brokerSilentGrace {
					if !silentLogged {
						silentLogged = true
						g.logf("已连上 broker 但 %v 内没收到任何状态，判定 GCU 后端未就绪",
							brokerSilentGrace)
					}
					g.setOnline(false)
					g.ensureBackend("收不到状态")
					// 这里刻意**不**断开重连：重连后那一次 GETSTATUS 跟本循环每 20 秒
					// 发的那次完全等价，断开只会多花一次握手、还让托盘图标闪一下。
					// 保持连接反复催，后端一活过来就能立刻收到状态。
				}
				client.Publish(topicFanCtrl, 0, false, `{"Action":"GETSTATUS"}`)
			}
		}
		client.Disconnect(60)
		g.setOnline(false)
		sleepOrStop(g.stopCh, 1500*time.Millisecond)
	}
}

// ---------------------------------------------------------------- GCU 后端自愈

// backendAllowed 读「自动拉起 GCU 服务」开关；未注入判断函数时视为允许
func (g *GCU) backendAllowed() bool {
	if g.autoBackend == nil {
		return true
	}
	return g.autoBackend()
}

// touchMsg 记一条状态报文到达。同时清空发布者失败名单 ——
// 既然已经有状态了，说明当前这份发布者是好的。
func (g *GCU) touchMsg() {
	g.mu.Lock()
	g.lastMsgAt = time.Now()
	g.pubFailed = map[string]bool{}
	g.mu.Unlock()
}

// serviceImagePath 读服务的 ImagePath（只读 HKLM，不需要管理员）
func serviceImagePath(name string) string {
	v, ok := regGetStringRO(hkeyLocalMachine,
		`SYSTEM\CurrentControlSet\Services\`+name, "ImagePath")
	if !ok {
		return ""
	}
	return strings.Trim(strings.TrimSpace(v), `"`)
}

// publisherCandidates 返回 GCUService.exe 的候选路径，按可信度排序。
//
// 组件是成套安装的：broker 在哪棵树里，配套的发布者就在同一棵树的 MyControlCenter 下。
// 所以先按服务的 ImagePath 反推，再退到几个已知的固定安装位置。
func publisherCandidates() []string {
	var out []string
	add := func(p string) {
		if p == "" {
			return
		}
		for _, e := range out {
			if strings.EqualFold(e, p) {
				return
			}
		}
		out = append(out, p)
	}

	if exe := serviceImagePath(gcuBridgeService); exe != "" {
		add(filepath.Join(filepath.Dir(exe), "MyControlCenter", gcuPublisherExe))
	}
	// L-Mechrevo 的默认安装位置（上面那步通常已经覆盖到，这里只是兜底）
	add(filepath.Join(`D:\Tools\L-Mechrevo\GCU\AiStoneService`, "MyControlCenter", gcuPublisherExe))
	// 官方控制中心自带的一份
	if pf := os.Getenv("ProgramFiles"); pf != "" {
		add(filepath.Join(pf, "OEM", "机械革命控制中心", "AiStoneService", "MyControlCenter", gcuPublisherExe))
	}
	return out
}

// nextPublisher 取下一个还没被判失败的候选路径；一轮全试完就清空名单重来
// （环境可能已经变了，比如服务被重启过）。
func (g *GCU) nextPublisher() string {
	cands := publisherCandidates()
	g.mu.Lock()
	defer g.mu.Unlock()

	if len(g.pubFailed) >= len(cands) {
		g.pubFailed = map[string]bool{}
	}
	for _, c := range cands {
		if g.pubFailed[c] {
			continue
		}
		if fi, err := os.Stat(c); err != nil || fi.IsDir() {
			g.pubFailed[c] = true // 文件都不在，直接排除
			continue
		}
		return c
	}
	g.pubFailed = map[string]bool{}
	return ""
}

func (g *GCU) markPublisherFailed(path string) {
	if path == "" {
		return
	}
	g.mu.Lock()
	g.pubFailed[path] = true
	g.mu.Unlock()
}

// reapPublisher 收拾「本程序拉起、但过了宽限期仍没带来任何状态」的发布者。
//
// 只动我们自己启动的那个 pid，并且要再确认一次映像名 —— pid 会被系统复用，
// 盲杀可能把别的进程干掉。
func (g *GCU) reapPublisher() {
	g.mu.Lock()
	pid, path, at := g.pubPID, g.pubPath, g.pubAt
	g.mu.Unlock()
	if pid == 0 || time.Since(at) < publisherGrace {
		return
	}
	if g.gotStatusSince(at) {
		return // 期间拿到过状态，说明它是有用的，留着
	}
	if processImageName(pid) != gcuPublisherExe {
		g.mu.Lock()
		g.pubPID, g.pubPath = 0, ""
		g.mu.Unlock()
		return // pid 已经不是它了，别碰
	}
	if err := terminateProcess(pid); err != nil {
		g.logf("结束无响应的 %s(pid=%d) 失败：%v", gcuPublisherExe, pid, err)
	} else {
		g.logf("已拉起的 %s(pid=%d) 在 %v 内没带来任何状态，收掉它、换下一个候选",
			gcuPublisherExe, pid, publisherGrace)
	}
	g.markPublisherFailed(path)
	g.mu.Lock()
	g.pubPID, g.pubPath = 0, ""
	g.mu.Unlock()
}

// startServiceAndWait 启动服务并等它进入运行状态，返回一句可直接写进日志的描述。
func startServiceAndWait(name string, wait time.Duration) (string, error) {
	h, err := openService(name, serviceQueryStatus|serviceStart)
	if err != nil {
		if errno, ok := err.(syscall.Errno); ok {
			switch errno {
			case errWinAccessDenied:
				return "", errors.New("拒绝访问（需要管理员权限）")
			case errSvcDisabled:
				return "", errors.New("服务已被禁用，需要先把启动类型改回「自动」或「手动」")
			case errSvcDoesNotExist:
				return "", errors.New("系统里没有这个服务")
			}
		}
		return "", err
	}
	defer h.close()

	var st serviceStatus
	read := func() uint32 {
		if r, _, _ := pQueryServiceStatus.Call(h.service, uintptr(unsafe.Pointer(&st))); r == 0 {
			return 0
		}
		return st.CurrentState
	}
	if read() == svcRunning {
		return "已在运行", nil
	}

	if r, _, e := pStartServiceW.Call(h.service, 0, 0); r == 0 {
		errno, _ := e.(syscall.Errno)
		switch errno {
		case errSvcAlreadyRunning:
			return "已在运行", nil
		case 0:
			return "", errors.New("StartService 失败但没有给出错误码")
		}
		return "", errno
	}

	deadline := time.Now().Add(wait)
	for time.Now().Before(deadline) {
		time.Sleep(400 * time.Millisecond)
		switch read() {
		case svcRunning:
			return "已启动", nil
		case svcStopped:
			// 刚点起来就又躺下了：多半是它自己启动失败（本机就是这样）
			if st.Win32ExitCode != 0 {
				return "", fmt.Errorf("启动后立刻退出（Win32 退出码 %d）", st.Win32ExitCode)
			}
		}
	}
	return "已发起启动请求（尚未进入运行状态）", nil
}

// ensureBackend 在 GCU 用不了的时候，尽量把后端（broker 服务 + 状态发布进程）拉回来。
//
// 为什么要做这件事：
//
//	GCUBridge 服务负责开 13688 端口，GCUService.exe 负责往里发 Fan/Status。
//	开机时 GCUBridge 会自己异常终止（系统日志事件 7023），而这个服务**没有**配置
//	失败恢复动作，所以一崩就永久躺平 —— 端口无人监听，本工具连都连不上。
//	（用户侧的表现就是：必须先手动打开一次机械革命控制台，控制台把它重新拉起来才行。）
//
// 这里按需把两者补起来，就不再依赖用户先去开控制台。
// 整个函数按 backendRetryInterval 节流；reapPublisher 只结束本程序自己启动的进程。
func (g *GCU) ensureBackend(reason string) {
	g.mu.Lock()
	if time.Since(g.backendAt) < backendRetryInterval {
		g.mu.Unlock()
		return
	}
	g.backendAt = time.Now()
	g.mu.Unlock()

	if !g.backendAllowed() {
		return
	}
	if !isElevated() {
		g.logf("GCU 未就绪（%s），但本程序不是管理员，无法自动拉起 GCU 服务；"+
			"可在设置里勾选「以管理员身份运行」", reason)
		return
	}

	g.reapPublisher()

	// 1) broker：把 GCUBridge 服务点起来
	if queryServiceState(gcuBridgeService) != svcRunning {
		if desc, err := startServiceAndWait(gcuBridgeService, 8*time.Second); err != nil {
			g.logf("拉起 %s 服务失败：%v", gcuBridgeService, err)
		} else if desc != "已在运行" {
			g.logf("%s 服务 %s", gcuBridgeService, desc)
		}
	}

	// 2) 发布者：GCUService.exe
	if pids := processPIDs(gcuPublisherExe); len(pids) > 0 {
		// 已经在跑（可能是我们自己起的，也可能是控制台起的）—— 别重复起，
		// 它会重复发状态。等它初始化就行。
		g.logf("GCU 未就绪（%s）；%s 已在运行（pid=%v），等它初始化（实测约 30 秒）",
			reason, gcuPublisherExe, pids)
		return
	}
	path := g.nextPublisher()
	if path == "" {
		g.logf("GCU 未就绪（%s），且找不到可用的 %s，无法自动恢复", reason, gcuPublisherExe)
		return
	}
	pid, err := launchHidden(path)
	if err != nil {
		g.markPublisherFailed(path)
		g.logf("启动 %s 失败（%s）：%v", gcuPublisherExe, path, err)
		return
	}
	g.mu.Lock()
	g.pubPID, g.pubPath, g.pubAt = pid, path, time.Now()
	g.mu.Unlock()
	g.logf("GCU 未就绪（%s），已拉起 %s (pid=%d)：%s，约 30 秒后应有状态",
		reason, gcuPublisherExe, pid, path)
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
	g.touchMsg()
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
	g.touchMsg()
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
