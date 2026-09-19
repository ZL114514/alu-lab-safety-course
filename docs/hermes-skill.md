---
name: alu-lab-safety-course
description: Use when 刷/查安徽大学实验室安全准入课程进度(172.17.109.74).
---

# 安徽大学实验室安全准入 · 课程进度工具

平台: `http://172.17.109.74`（JEECG-BOOT + ant-design-vue SPA，后端 `http://172.17.109.74:8080/jeecg-boot`）。
**范围**：只做「学习任务」（把必修课的学习进度按播放器真实流程上报）。
**不含**：考试答题、承诺书签名 —— 这两项用户明确要自己做，不要写进自动化。

## 快速开始

```bash
# 1) 取验证码（服务端返回 base64，需视觉识别）
python alu.py captcha          # 打印 {"key":..., "image":"cap_big.png"(8x放大)}
#    用 vision 读 cap_big.png 的 4 位字符（大小写敏感）
python alu.py login <CODE>     # 成功则 token 存进 state.json
python alu.py status           # 只读：完成度 / 考试门槛 / 已考次数与最高分
python alu.py sync             # 重建 courses.json（课程树+弹题+ejectTime+duration）
python alu.py run              # 完成所有未学课程（幂等，可中断续跑）
```

`run` **默认按每门视频的真实时长等待**，节奏与播放器一致：
`updateVisits` → 等到该题 `ejectTime` 才交弹题（一题一包） → 等满视频时长 → `finishRate` → `finish`。
所以整批 73 门 ≈ **5.6 小时**，务必挂后台跑，别用前台等待。

常用变体：`run --limit 5`、`run --course <cid>`、`run --dry-run`（只打印计划与 ETA）、
`run --retry`（强制重跑已完成课程）、`run --speed 60`（调试倍速，60=快 60 倍）、`run --no-wait`（完全不等待，仅测试）。
环境变量覆盖：`ALU_BASE` / `ALU_GRAPH` / `ALU_USER` / `ALU_PASS` / `ALU_TOKEN`。

## 关键事实（别重新逆向）

- 登录：`POST /sys/userLogin` body `{username, password, captcha, checkKey, remember_me}`。
  密码是 **AES-128-ECB / Pkcs7 / base64**，密钥就是前端常量 `1234567890abcdef1234567890abcdef`；
  `alu.py login` 内部用 `aes_encrypt()` 现算，所以 state.json 存明文密码即可（新账号直接换 username/password）。
- 验证码：`GET /sys/randomImage/{ms 时间戳}`，`result` 是 data URL，`checkKey` = 那个时间戳。**必须视觉识别**（无 OCR 库可靠）。
- 课程树（含本人状态）：`GET /jcedutec/courseSource/myCourseTypeTree?graphId=<GID>`，
  节点 title 前缀 `(学完)` / `(未学)` 是**本人**完成状态的唯一可靠来源（`queryById` 的 isFinish 恒为 null）。
  基础课程 graphId = `2092848628543557633`。
- 单课详情/时长：`GET /jcedutec/courseSource/queryById?id=<cid>` → `duration`（float 字符串）。
- 课程弹题（**含正确答案**）：`GET /jcedutec/courseSource/queryCourseQuestionRelaByMainId?id=<cid>`
  → `questionId`、`kind`（`1`判断 `2`单选 `3`多选）、`correctAnswer`（`"A"` 或 `"A,B,C,D"`）、`ejectTime`。
- 完成一门课的真实顺序（照着播放器的调用做）：
  1. `POST /jcedutec/courseSource/updateVisits`  `{id, graphId}`
  2. `POST /jcedutec/courseSource/submitAnswer`  `{questionId, id, graphId, option}`（每题一次；多选 option 传**数组** `["A","B"]`，单选/判断传字符串）
  3. `POST /jcedutec/courseSource/finishRate`    `{id, graphId, watchDuration}` ← **必须整数**
  4. `POST /jcedutec/courseSource/finish`        `{id, graphId}`
- 考试门槛/次数（只读）：`GET /jcedutec/exam/myExamList` → `learnTime`(需 ≥90 分钟)、`learnRate`(≥80%)、`examTime`、`qualifiedScore`(≥90)、`useCount`、`score`。
- 本人信息：`GET /students/queryMyInfo` → `name`、`code`、`commitmentStatus`(1=已签承诺书)。
- 考试相关（**本技能不使用**，仅备查）：`/jcedutec/exam/startExam`、`/jcedutec/exam/submitExam`；试卷返回里 `correctAnswer` 是 null（答案不下发）。

## 陷阱

- **`watchDuration` 传整数**：传 `229.646009` 会 500 `java.lang.NumberFormatException`，学习时长统计不上。
  脚本用 `int(duration) + 1`（服务端按视频长度截断）。
- **POST 别把 body 拼进 query string**：踩过一次 —— 服务端报 `Required request body is missing`。
  本脚本 `req(path, method, params=, body=)` 的 `body` 一定用关键字传。
- **token 寿命短**（1~2 小时，跨天必失效）：401 时脚本抛提示，重新 `captcha` + `login` 即可。
  注意 token 寿命 **远短于** 按真实时长跑完全部课程的 5.6 小时 —— 中途必然 401 中断，属预期：
  重新登录后再 `run` 就续跑（已完成课程跳过；正在等的那门按 `state['wait']` 记录的开始时间**接着等剩余时长**，不重头等）。也可 `--limit` 分批跑。
- **重建课程表别丢 `ejectTime`**：`queryCourseQuestionRelaByMainId` 的每题弹题时刻（`"40"`、`"210"` …）必须**落进 courses.json**，
  否则弹题全在 0 秒发出，节奏一眼不像人看的（踩过一次：重建表时漏了这字段，69 门课全变 0 秒）。
- 请求加 0.1~0.6s 间隔与 4 次退避重试：服务器会偶发连接超时。
- 页面路由是前端硬编码（`/students/questionList`、`/students/signature`…），`/sys/permission/getUserPermissionByToken` 只返回「首页」，别指望从菜单摸模块。

## 文件

- `alu.py` — 全部逻辑（captcha / login / sync / status / run）
- `state.json` — 运行时生成：`{user, password, token, capkey, progress, wait}`（`wait` = 等待/续跑记账），**含明文密码与 token，别外传/别提交**
- `courses.json` — 课程表缓存（`sync` 重建；schema 变了会自动重建）；`cap.jpg` / `cap_big.png` — 验证码

## 验证

跑完 `run` 后：`sync` 应显示 `(73 done, 0 todo)`，或 `status` 显示 `courses: 73/73 done`；
服务端 `myCourseTypeTree` 里对应课程 title 变成 `(学完)`。
