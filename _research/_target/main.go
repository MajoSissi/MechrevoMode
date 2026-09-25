// 自检用的无害目标进程：什么都不做，睡 60 秒。
//
// 专门给 MechrevoMode.exe -test-gcu-launch <exe> 当靶子用：
// 验完「能静默起来 / 能按 pid 认出映像名 / 能收掉」之后不该在系统里留任何痕迹。
package main

import "time"

func main() {
	time.Sleep(60 * time.Second)
}
