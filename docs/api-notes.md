# 平台接口与逆向笔记

抓取时间：**2026-09**。平台为 JEECG-BOOT + Vue SPA，接口随时可能改版。
改版后重新抓取的方法见文末「如何重新抓接口」。

后端基址：`http://172.17.109.74:8080/jeecg-boot`
鉴权：请求头 `X-Access-Token: <登录返回的 token>`

---

## 1. 登录 / 验证码

### `GET /sys/randomImage/{时间戳毫秒}?_t={秒}`
无需 token。返回：

```json
{ "success": true, "result": "data:image/png;base64,iVBORw0..." }
```

- `{时间戳毫秒}` 同时就是登录时的 `checkKey`。
- 验证码图片是扭曲图形，未见可用的纯 OCR 方案，人工/视觉模型识别最可靠。

### `POST /sys/userLogin`
请求体：

```json
{
  "username": "工号",
  "password": "AES 加密后的 base64 字符串",
  "captcha": "4位验证码",
  "checkKey": "上一步的时间戳",
  "remember_me": true
}
```

密码加密方式（从前端 chunk 里读出来的）：

- 算法：**AES-128-ECB，PKCS7 padding，输出 base64**
- 密钥（前端硬编码常量）：`1234567890abcdef1234567890abcdef`
- 等价 JS：`CryptoJS.AES.encrypt(CryptoJS.enc.Utf8.parse(pwd), CryptoJS.enc.Utf8.parse("1234567890abcdef1234567890abcdef"), {mode: ECB, padding: Pkcs7}).toString()`

`alu.py` 内的 `aes_encrypt()` 用 Python 复刻了这一段，所以配置里填明文密码即可。

返回：`result.token`（约 137 字符）、`result.userInfo.realname`。
**token 寿命约 1~2 小时**，过期后所有业务接口返回 `code:401 / "Token失效，请重新登录!"`（HTTP 状态是 401）。

---

## 2. 课程 / 学习任务

### `GET /jcedutec/courseSource/myCourseTypeTree?graphId={GID}`
课程分类树（**返回的是当前登录学生的状态**）。基础课程 `GID = 2092848628543557633`。

结构：`result[]` 为分类 → `children[]` 为课程，课程节点：

```json
{ "key": "1818524607139721218", "title": "(学完) JCXL01《高校实验室分类与特点》", "label": "1" }
```

- `key` = 课程 id（cid）
- **`title` 前缀 `(未学)` / `(学完)` 是学生完成状态的唯一可靠来源**；`queryById` 返回的 `isFinish` 恒为 `null`。

### `GET /jcedutec/courseSource/queryById?id={cid}`
课程详情：`name`、`url`（视频相对路径）、`duration`（**浮点字符串**，如 `"229.646009"`）、`remark`。

### `GET /jcedutec/courseSource/queryCourseQuestionRelaByMainId?id={cid}`
该课程的弹题（**含正确答案**）：

| 字段 | 说明 |
|---|---|
| `questionId` | 题目 id |
| `kind` | `1`=判断、`2`=单选、`3`=多选 |
| `correctAnswer` | `"A"`；多选为 `"A,B,C,D"` |
| `ejectTime` | **弹题时刻（视频播放到第几秒弹出）**，如 `"40"`、`"210"` |
| `stem` | 题干；选项为 `optiona`~`optiond` |

> 坑：重建课程表时**必须带上 `ejectTime`**，否则弹题全在 0 秒发出，时序一眼不像真人。

### 完成一门课的调用序列（POST，JSON body）

| # | 接口 | body | 说明 |
|---|---|---|---|
| 1 | `/jcedutec/courseSource/updateVisits` | `{"id": cid, "graphId": GID}` | 记一次访问 |
| 2 | `/jcedutec/courseSource/submitAnswer` | `{"questionId": qid, "id": cid, "graphId": GID, "option": opt}` | 每题一次；**多选 `option` 传数组** `["A","B"]`，判断/单选传字符串 `"A"` |
| 3 | `/jcedutec/courseSource/finishRate` | `{"id": cid, "graphId": GID, "watchDuration": 整数秒}` | **必须整数** |
| 4 | `/jcedutec/courseSource/finish` | `{"id": cid, "graphId": GID}` | 标记完成 |

成功响应：`{"success": true, "message": "提交成功！"/"添加成功！"}`

**踩过的坑**：

- `watchDuration` 传 `229.646009` → HTTP 500，`java.lang.NumberFormatException: For input string: "229...`，
  学习时长统计不上。传整数（脚本用 `int(duration) + 1`，服务端会按视频真实长度截断）。
- POST 的 JSON 若被拼进 query string（而不是 body）→ `Required request body is missing`。

---

## 3. 考试 / 证书（只读查询，本工具不调用考试接口）

| 接口 | 用途 |
|---|---|
| `GET /jcedutec/exam/myExamList` | 考试配置与本人成绩：`learnTime`(门槛学习分钟)、`learnRate`(门槛进度%)、`examTime`(考试分钟)、`qualifiedScore`(合格分)、`examType`、`useCount`(已考次数)、`score`(最高分)、`startTime`/`endTime`(考试窗口) |
| `GET /students/queryMyInfo` | 本人信息：`name`、`code`、`commitmentPath`(承诺书文件)、`commitmentStatus`(1=已签) |
| `POST /jcedutec/exam/startExam` | 开考（**本工具不调用**） |
| `POST /jcedutec/exam/submitExam` | 交卷（**本工具不调用**） |
| `POST /students/uploadCommitment` | 上传承诺书签名图，multipart 字段名 `file`（**本工具不调用**） |

- `startExam` 返回的试卷为 50 题、`examTime` 分钟；题目对象里 **`correctAnswer` 为 `null`**（答案不下发到前端）。
- 考试窗口示例：`2026-09-01 12:00 ~ 2026-10-15 23:59`；`useCount` 会随开考次数累加，允许多次重考。

---

## 4. 如何重新抓接口（平台改版后）

页面路由是**前端硬编码**的，`/sys/permission/getUserPermissionByToken` 只返回「首页」，别指望从菜单摸模块。做法：

1. 打开 `http://172.17.109.74`，浏览器 DevTools → Network 过滤 `jeecg-boot`，手工点一遍目标功能，抄下请求 URL/body。
2. 或抓前端包：从 `app.js` 里的 chunk 映射表取出全部 `chunk-*.js` 下载后本地 grep：
   ```bash
   grep -rho '"/[a-zA-Z0-9/_-]*"' chunks/*.js | sort -u | grep -i 'course\|exam\|studen'
   grep -rho '.\{300\}startExam.\{200\}' chunks/*.js     # 看调用上下文与参数
   ```
3. 业务模块的接口前缀是 `/jcedutec/`（课程/考试）与 `/students/`（学生个人）。
