# CoPaw 新增 Channel 通道开发指南

本文档以微信 iLink Bot 通道（PR #2260）为参考案例，总结在 CoPaw 中新增一个完整 Channel 的全流程，供后续开发及 AI 参考。

---

## 目录

1. [整体架构](#整体架构)
2. [Step 1：后端 — 创建 channel 包](#step-1后端--创建-channel-包)
3. [Step 2：后端 — 注册通道](#step-2后端--注册通道)
4. [Step 3：后端 — 配置类](#step-3后端--配置类)
5. [Step 4：后端 — 扫码登录路由（可选）](#step-4后端--扫码登录路由可选)
6. [Step 5：前端 — 常量与表单](#step-5前端--常量与表单)
7. [Step 6：前端 — 国际化 i18n](#step-6前端--国际化-i18n)
8. [Step 7：文档](#step-7文档)
9. [Step 8：CI/pre-commit 检查](#step-8cipre-commit-检查)
10. [Step 9：PR 提交与 Review 响应](#step-9pr-提交与-review-响应)
11. [常见坑与最佳实践](#常见坑与最佳实践)

---

## 整体架构

```
新增通道涉及的文件清单（以 weixin 为例）：

后端：
  src/copaw/app/channels/<name>/__init__.py    # 导出 XxxChannel
  src/copaw/app/channels/<name>/client.py      # HTTP 客户端封装
  src/copaw/app/channels/<name>/channel.py     # 主通道逻辑，继承 BaseChannel
  src/copaw/app/channels/<name>/utils.py       # 工具函数（加解密、header 等）
  src/copaw/app/channels/registry.py           # 注册通道名 → 类的映射
  src/copaw/config/config.py                   # 新增 XxxConfig 类，注入 ChannelConfig
  src/copaw/app/routers/config.py              # 扫码登录等额外 API（可选）

前端：
  console/src/pages/Control/Channels/components/constants.ts     # 通道名→显示名
  console/src/pages/Control/Channels/components/ChannelDrawer.tsx # 表单配置项
  console/src/locales/zh.json                  # 中文翻译
  console/src/locales/en.json                  # 英文翻译
  console/src/locales/ja.json                  # 日文翻译
  console/src/locales/ru.json                  # 俄文翻译

文档：
  website/public/docs/channels.zh.md
  website/public/docs/channels.en.md
```

---

## Step 1：后端 — 创建 channel 包

### 1.1 `__init__.py`

```python
from .channel import XxxChannel

__all__ = ["XxxChannel"]
```

### 1.2 `client.py` — HTTP 客户端

- 用 `httpx.AsyncClient` 封装所有 API 调用
- 实现 `start()` / `stop()` 管理 client 生命周期
- 典型方法：`getupdates(cursor)`（长轮询）、`sendmessage(...)` 、`download_media(...)`
- 请求头、鉴权、重试逻辑都封装在此

### 1.3 `channel.py` — 主通道逻辑

继承 `BaseChannel`，核心要点：

```python
class XxxChannel(BaseChannel):
    channel = "xxx"          # 必须与前端 constants.ts 中的 key 一致！

    def __init__(self, ...): ...

    async def start(self) -> None:
        # 加载持久化 token、context_tokens 等
        # 启动长轮询线程
        ...

    async def stop(self) -> None: ...

    def _poll_loop(self) -> None:
        # 独立线程，运行独立 event loop
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self._poll_loop_async())

    async def _poll_loop_async(self) -> None:
        # 长轮询主循环
        # ret=-1 = 正常超时（无新消息），用 DEBUG 级别记录，不重试
        # 其他非 0 ret = 真实错误，用 WARNING + sleep 重试
        ...

    async def _on_message(self, msg, client) -> None:
        # 解析消息 → 构建 meta → enqueue
        # 同时缓存 context_token（用于 heartbeat/cron 主动发送）
        ...

    async def send(self, to_handle, text, meta) -> None:
        # meta 里取 context_token；没有则从缓存取（主动发送场景）
        context_token = meta.get("xxx_context_token", "") or (
            self._user_context_tokens.get(to_user_id, "")
        )
        ...

    async def send_content_parts(self, to_handle, parts, meta) -> None: ...
```

**关键设计：**
- `session_id` 格式统一为 `<channel_name>:<user_id>`，群聊为 `<channel_name>:group:<group_id>`
- `context_token` 需要按 `user_id` 缓存并持久化（JSON 文件），支持重启后 heartbeat/cron 继续工作
- `channel` 字段值必须与前端保持一致（cron/heartbeat 通过此名称查找通道）

### 1.4 `utils.py` — 工具函数

- 头部生成、加解密、文本分割等
- 行长度严格遵守项目 flake8 限制（默认 **79 字符**）

---

## Step 2：后端 — 注册通道

文件：`src/copaw/app/channels/registry.py`

```python
CHANNEL_REGISTRY: Dict[str, Tuple[str, str]] = {
    # ...existing...
    "xxx": (".xxx", "XxxChannel"),
}
```

---

## Step 3：后端 — 配置类

文件：`src/copaw/config/config.py`

```python
class XxxConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    bot_token_file: str = ""
    media_dir: str = ""
    # ...其他字段

class ChannelConfig(BaseModel):
    # ...existing...
    xxx: XxxConfig = XxxConfig()
```

---

## Step 4：后端 — 扫码登录路由（可选）

文件：`src/copaw/app/routers/config.py`

如果通道需要扫码登录（如微信），在此文件添加 `/channels/xxx/qrcode` 和 `/channels/xxx/qrcode/status` 两个接口。

**注意 import 顺序**（pylint C0411 规则）：
```python
# 标准库放最前
from datetime import datetime, timezone
from typing import Any

# 第三方库放后
import segno
import httpx
```

---

## Step 5：前端 — 常量与表单

### 5.1 `constants.ts`

```typescript
export const CHANNEL_LABELS: Record<string, string> = {
  // ...existing...
  xxx: "Xxx Channel",
};
```

### 5.2 `ChannelDrawer.tsx`

在 `renderChannelForm()` 函数中新增 `case "xxx":`，渲染配置表单项。

- 翻译 key 用 `t("channels.xxxFieldName")` 形式
- 有文档链接则在 `CHANNEL_DOC_ZH_URLS` / `CHANNEL_DOC_EN_URLS` 中添加对应锚点
- **构建前端**：修改后需要重新 build 并复制到运行时目录：
  ```bash
  cd console && npm run build
  cp -r dist/* ../src/copaw/console/
  ```

---

## Step 6：前端 — 国际化 i18n

需同步修改 **4 个** locale 文件：

- `console/src/locales/zh.json`
- `console/src/locales/en.json`
- `console/src/locales/ja.json`
- `console/src/locales/ru.json`

每个文件在 `"channels"` 节点下添加对应键值，至少补充 zh/en，ja/ru 可用 en 内容占位。

---

## Step 7：文档

文件：
- `website/public/docs/channels.zh.md`
- `website/public/docs/channels.en.md`

章节内容需包含：
1. 工作原理概述
2. 前置准备（账号申请、依赖安装等）
3. 配置说明（字段表格）
4. 完整 `config.json` 示例
5. 多模态能力说明（支持哪些媒体类型）

同时更新文档末尾的**附录汇总表格**（配置总览表、多模态支持表）。

---

## Step 8：CI/pre-commit 检查

### 本地验证命令

```bash
# 只检查修改的文件（速度快）
uv tool run pre-commit run --files src/copaw/app/channels/<name>/channel.py \
  src/copaw/app/channels/<name>/utils.py \
  src/copaw/app/routers/config.py

# 如有自动修复（black/add-trailing-comma），再跑一次确认全过
uv tool run pre-commit run --files <同上>
```

### 常见 CI 失败原因与修复

| 错误 | 原因 | 修复 |
|------|------|------|
| `add-trailing-comma` modified files | 函数调用/定义缺少尾逗号 | 让 hook 自动修复后 commit |
| `flake8 E501` | 行超过 79 字符 | 换行，注意注释行也算 |
| `pylint C0411` | import 顺序错误（标准库 < 第三方 < 本地） | 调整 import 顺序 |
| `pylint C0415` | import 在函数内部 | 移到文件顶层 |
| `mypy` 类型错误 | 类型标注不完整或不兼容 | 补充类型标注 |
| `prettier` 格式错误 | 前端 TS/JSON 格式问题 | 本地运行 `prettier --write` |

---

## Step 9：PR 提交与 Review 响应

### 提交 PR

```bash
gh pr create \
  --repo agentscope-ai/CoPaw \
  --title "feat: add Xxx channel" \
  --body "$(cat pr_body.md)" \
  --base main
```

### Review 响应原则

1. **最小化改动**：只修复 reviewer 明确指出的问题，不扩大修改范围
2. **每次 fix 后本地跑 pre-commit**，确认通过再 push
3. **用 `gh pr comment` 回复 reviewer**：说明做了什么、为什么这样做
4. 如果过度修改了文件，用 `git checkout HEAD -- <file>` 回退再重做

### 用 gh 操作

```bash
# 回复 PR 评论
gh pr comment <PR_NUMBER> --repo agentscope-ai/CoPaw --body "..."

# 查看 PR 状态
gh pr view <PR_NUMBER> --repo agentscope-ai/CoPaw
```

---

## 常见坑与最佳实践

### 通道 `channel` 字段命名

`channel.py` 中 `channel = "xxx"` 的值必须与前端 `constants.ts` 的 key **完全一致**，否则 cron/heartbeat 找不到通道。

### context_token 主动发送

heartbeat/cron 发送时 `meta` 是空的，必须从缓存取 `context_token`：

```python
context_token = meta.get("xxx_context_token", "") or (
    self._user_context_tokens.get(to_user_id, "")
)
```

同时持久化到 JSON 文件（重启后依然有效）：

```python
# 收到消息时
self._user_context_tokens[from_user_id] = context_token
self._save_context_tokens()

# start() 时
self._load_context_tokens()
```

### 长轮询日志

`ret=-1` 是正常超时（无新消息），用 `DEBUG` 级别，不要用 `WARNING`，否则空闲期会刷屏。

### 行长度

项目 `.flake8` 限制是 **79 字符**，连注释行都算。写代码时注意换行。

### media 文件名

下载媒体文件时，文件名用内容哈希而非原始 URL，避免多用户同名文件互相覆盖：

```python
url_hash = encrypt_query_param(url)  # 不要直接用 hashlib.md5(url)
```

### 前端构建

修改 `ChannelDrawer.tsx` 或 locale 文件后，记得重新 build 并复制产物，否则后端服务的静态文件不会更新。
