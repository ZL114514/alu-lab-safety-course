# 安徽大学实验室安全准入 · 课程进度工具（alu_lab）

自动完成「必修课程」的学习任务 —— 按网页播放器的**真实时序**上报学习进度：
逐门课等待视频实际时长、按弹题时刻 `ejectTime` 逐个提交，使记录看起来与真人观看一致。

> **范围边界**：本工具**只做学习任务**。
> **不含**考试答题、也不含承诺书签名 —— 这两项请本人在网页完成（脚本内没有任何考试/签名逻辑）。

---

## 1. 平台信息

| 项 | 值 |
|---|---|
| 前端 | `http://172.17.109.74`（须校内网 / 校内 VPN） |
| 后端 API | `http://172.17.109.74:8080/jeecg-boot` |
| 框架 | JEECG-BOOT + Vue(ant-design-vue) 单页应用 |
| 登录 | 工号 + 密码（初始密码=学/工号），**带图形验证码** |
| 考试门槛 | 学习时长 ≥ 90 分钟、学习进度 ≥ 80%、60 分钟、≥ 90 分合格 |
| 课程规模 | 基础课程 graphId = `2092848628543557633`，共 73 门、合计约 335 分钟 |

---

## 2. 环境要求

- Python 3.10+
- 依赖：`cryptography`、`Pillow`

```bash
pip install -r requirements.txt
```

能访问 `172.17.109.74`（校园网内网地址，校外需先连 VPN）。

---

## 3. 安装与首次配置

```bash
git clone <此仓库地址> alu-lab-safety-course
cd alu-lab-safety-course
pip install -r requirements.txt
cp state.example.json state.json
```

编辑 `state.json`，填自己的账号密码：

```json
{
  "user": "你的学号或工号",
  "password": "你的密码（初始密码=学/工号）",
  "token": "",
  "capkey": null
}
```

> `token` / `capkey` 运行时自动写入，不用手填。

---

## 4. 首次使用：登录（唯一需要人工的一步）

验证码是扭曲变形图形，**脚本不做 OCR**，需要人（或 AI agent 的视觉能力）看图输入：

```bash
# 第一步：拉取验证码，会在脚本目录生成 cap.jpg 与 cap_big.png（8 倍放大便于辨认）
python alu.py captcha
# 输出示例：{"key": "1789792098929", "image": "..../cap_big.png", "raw": "..../cap.jpg"}

# 第二步：打开 cap_big.png，读出 4 个字符（大小写敏感！），作为参数登录
python alu.py login ab3D
# 成功：login OK  realname=张三  token_len=137
```

> 用 AI agent（Hermes / Claude 等）操作时，让它 **vision 读取 `cap_big.png`** 再把 4 个字符传给 `login`，全程无需人工。
> 验证码有效期短：`captcha` 之后立刻 `login`，失败就重新 `captcha` 拿新的。

登录成功后 token 存在 `state.json`，**之后每次重登只需验证码**，不用再输密码。

---

## 5. 命令参考

```bash
python alu.py <命令> [参数]
```

| 命令 | 说明 | 备注 |
|---|---|---|
| `captcha` | 拉验证码 → `cap.jpg` + `cap_big.png`(8x)，并记下 `checkKey` | 登录前必跑 |
| `login <CODE>` | 用待用验证码登录，token 写入 `state.json` | 验证码大小写敏感 |
| `sync` | 重建课程表 `courses.json`（课程树 + 每门课弹题与 `ejectTime` + 视频时长） | 学期/课程变动后跑一次 |
| `status` | 只读汇总：完成度、考试门槛、已考次数与最高分、承诺书状态 | 不修改任何东西 |
| `run` | 完成未学课程（幂等、可中断续跑） | **默认按视频真实时长等待** |

`run` 的参数：

| 参数 | 作用 |
|---|---|
| （无） | 所有未学课程，按真实时长等待 |
| `--limit N` | 只处理前 N 门（配合 token 寿命分批跑） |
| `--course <cid>` | 只处理指定课程（cid 见 `courses.json` 或 `sync` 输出） |
| `--dry-run` | 只打印计划与预计耗时，不发任何包 |
| `--retry` | 强制重跑**已完成**的课程（默认跳过） |
| `--speed N` | 等待倍速，**仅调试用**：`60` = 快 60 倍，`1.0`（默认）= 真实时长 |
| `--no-wait` | 完全不等待，立刻发包（仅测试/重跑用，**正式使用别加**） |

环境变量覆盖（可选）：`ALU_BASE`、`ALU_GRAPH`、`ALU_USER`、`ALU_PASS`、`ALU_TOKEN`。

常用示例：

```bash
python alu.py status                      # 看现在什么情况
python alu.py run --dry-run               # 先看计划与预计耗时
python alu.py run --limit 15              # 分批：一次 15 门
python alu.py run --course 1818542963335016450
```

---

## 6. 运行机制（为什么它很慢）

每门课的完整时序（与网页播放器一致）：

```
updateVisits                       # 进入课程，记一次访问
   ↓  等到该题 ejectTime 时刻
submitAnswer × N                   # 弹题一题一包，在第 40s / 210s … 才发
   ↓  继续等到视频总时长结束
finishRate {watchDuration}         # 上报观看时长（整数秒）
finish                             # 标记该课完成
```

- **等待时长 = 各视频实际时长**，所以 73 门全量约 **5.6 小时**。
- **续跑记账**：正在等待的课程会把开始时间写进 `state.json` 的 `wait` 字段。
  中断后再跑会**接着剩余时长等**（不会重头等），已完成课程直接跳过。
- 每 30 秒打印一次心跳（`remaining 0:03:49`），便于确认它在正常等待。
- 每个请求间隔 0.1~0.6 秒并带 4 次退避重试，避免服务器偶发超时导致任务中断。

### ⚠️ token 寿命 vs 长跑（重要）

token 只有 **1~2 小时**寿命，而全量真实时长等待需要 **5.6 小时** —— 中途必然出现
`token expired, run: python alu.py captcha && python alu.py login <CODE>`，**这是预期行为**。

处理方式（任选）：

```bash
# 方式 A：分批跑
python alu.py run --limit 20        # 跑 20 门；token 过期后重新登录再跑，自动跳过已完成
# 方式 B：挂后台整批跑，中断后重新登录再 run 一次即可续跑
```

---

## 7. 全量运行建议

```bash
# 1) 先确认状态与规模
python alu.py status
python alu.py run --dry-run          # 打印总耗时估算

# 2) 挂后台跑（Linux/macOS）
nohup python -u alu.py run > run.log 2>&1 &
tail -f run.log

# Windows（PowerShell）
Start-Process -NoNewWindow python -ArgumentList "-u","alu.py","run"

# 3) 完成后核对
python alu.py sync                   # 应显示 "(73 done, 0 todo)"
python alu.py status                 # courses: 73/73 done
```
服务端核对：课程树里对应课程标题会从 `(未学)` 变为 `(学完)`。

---

## 8. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `Token失效，请重新登录!` / `401` | token 过期（1~2 小时） | `python alu.py captcha` → 读图 → `login <CODE>` → 再 `run` |
| `登录失败` / `验证码错误` | 验证码过期或看错（区分大小写） | 重新 `captcha` 再登录 |
| `java.lang.NumberFormatException` | `watchDuration` 传了小数 | 用本仓库版本（已传整数） |
| `Required request body is missing` | POST 请求体被拼进了 query string | 用本仓库版本（`body=` 关键字传参） |
| 连接超时 / 偶发失败 | 服务器抖动 | 脚本已含 4 次退避重试；连续失败就歇几分钟 |
| 某门课弹题全在 0 秒发出 | `courses.json` 缺 `ejectTime` 字段 | 删掉 `courses.json` 重新 `python alu.py sync` |
| 课程跑完仍是 `(未学)` | 上报中断/失败 | `python alu.py run --course <cid> --retry` 重跑该门 |

---

## 9. 安全提示

- `state.json` 里有**明文账号密码与 token**，本仓库已把它写进 `.gitignore`，**永远不要提交或外传**。
- `courses.json`、`cap.jpg`、`cap_big.png` 同样是本地运行产物，已忽略。
- 建议在校园网内使用；不要把 token 贴到任何公开处。

---

## 10. 目录结构

```
alu-lab-safety-course/
├── alu.py                 # 全部逻辑：captcha / login / sync / status / run
├── README.md              # 本文档
├── requirements.txt       # cryptography, Pillow
├── state.example.json     # 配置模板（复制为 state.json 后填账号）
├── .gitignore             # 忽略 token / 课程缓存 / 验证码图
└── docs/
    └── api-notes.md       # 平台接口与逆向笔记（接口表、参数、坑）
```

运行时另外生成（不入库）：`state.json`、`courses.json`、`cap.jpg`、`cap_big.png`。

---

## 11. 接口笔记

平台接口、参数、字段含义与逆向方法见 [`docs/api-notes.md`](docs/api-notes.md)。
接口为 2026-09 抓取结果，若平台改版需按该文档的方法重新抓取（前端 chunk 里搜接口路径）。

---

## 12. 边界声明

- 本工具仅用于**本人账号**的学习任务上报，不含考试答题与承诺书签名。
- 学术诚信与考试规则请遵守平台要求；考试请自行完成。
