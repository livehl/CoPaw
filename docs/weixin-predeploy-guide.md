# 微信配置与模型预置部署指南

本文档介绍如何预置配置微信 iLink Bot 和模型，实现开箱即用，让用户直接在微信中就能使用 AI 助手。

---

## 目录

1. [目录结构概览](#目录结构概览)
2. [微信 Token 获取与保存](#微信-token-获取与保存)
3. [模型预置配置](#模型预置配置)
4. [完整预置配置示例](#完整预置配置示例)
5. [Docker 部署方案](#docker-部署方案)
6. [验证部署](#验证部署)

---

## 目录结构概览

CoPaw 使用以下目录结构存储配置：

```
~/.copaw/                          # 工作目录（可通过 COPAW_WORKING_DIR 修改）
├── config.json                    # 全局配置（智能体列表、环境变量）
├── weixin_bot_token               # 微信 iLink Bot Token（扫码登录后自动生成）
└── workspaces/
    └── default/                   # 默认智能体工作区
        ├── agent.json             # 智能体配置（渠道、模型等）
        ├── chats.json             # 对话历史
        ├── AGENTS.md              # 智能体人设文件
        └── memory/                # 记忆文件

~/.copaw.secret/                   # 敏感数据目录（可通过 COPAW_SECRET_DIR 修改）
└── providers/                     # 模型提供商配置
    ├── builtin/                   # 内置提供商配置
    │   ├── modelscope.json
    │   ├── dashscope.json
    │   └── ...
    └── active_llm.json            # 当前激活的 LLM 配置
```

---

## 微信 Token 获取与保存

微信 iLink Bot 使用官方 HTTP API，**无需 SDK**，直接通过 REST API 调用即可获取 Token。

### iLink API 基础信息

| 项目 | 值 |
|------|-----|
| **API 基础地址** | `https://ilinkai.weixin.qq.com` |
| **协议** | HTTP/JSON |
| **认证方式** | Bearer Token（扫码后获取） |

### 获取 Token 完整流程

#### 步骤 1：获取登录二维码

**请求：**
```http
GET https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3
```

**请求头：**
```http
Content-Type: application/json
AuthorizationType: ilink_bot_token
X-WECHAT-UIN: {base64编码的随机数}
```

> **X-WECHAT-UIN 生成方法：** `base64(str(random.randint(0, 0xFFFFFFFF)))`

**响应示例：**
```json
{
  "ret": 0,
  "qrcode": "weixin://dl/business/?t=xxx",
  "qrcode_img_content": "data:image/png;base64,iVBORw0KG..."
}
```

| 字段 | 说明 |
|------|------|
| `ret` | 返回码，0 表示成功 |
| `qrcode` | 二维码内容（微信扫码链接） |
| `qrcode_img_content` | 二维码图片 Base64 数据 |

---

#### 步骤 2：轮询扫码状态

**请求：**
```http
GET https://ilinkai.weixin.qq.com/ilink/bot/get_qrcode_status?qrcode={qrcode}
```

**响应示例（等待中）：**
```json
{
  "ret": 0,
  "status": "waiting"
}
```

**响应示例（已确认）：**
```json
{
  "ret": 0,
  "status": "confirmed",
  "bot_token": "eyJhbGciOiJIUzI1NiIs...",
  "baseurl": "https://ilinkai.weixin.qq.com"
}
```

**响应示例（已过期）：**
```json
{
  "ret": 0,
  "status": "expired"
}
```

| 状态 | 说明 |
|------|------|
| `waiting` | 等待用户扫码 |
| `confirmed` | 扫码成功，返回 bot_token |
| `expired` | 二维码已过期（约5分钟） |

---

#### 步骤 3：保存 Token

获取到 `bot_token` 后，保存到文件供后续使用：

```bash
# 保存到文件
echo "eyJhbGciOiJIUzI1NiIs..." > ~/.copaw/weixin_bot_token

# 设置权限
chmod 600 ~/.copaw/weixin_bot_token
```

---

### 完整实现代码（Python）

**无需任何外部依赖，仅用标准库 + requests：**

```python
#!/usr/bin/env python3
"""
微信 iLink Bot Token 获取 - 独立实现
不依赖 CoPaw，纯 HTTP API 调用
"""

import base64
import json
import random
import time
from pathlib import Path

import requests  # 仅需安装: pip install requests


class WeixiniLinkAuth:
    """微信 iLink Bot 认证客户端"""
    
    BASE_URL = "https://ilinkai.weixin.qq.com"
    
    def __init__(self):
        self.session = requests.Session()
    
    def _make_headers(self, bot_token: str = "") -> dict:
        """生成请求头"""
        # X-WECHAT-UIN: base64(str(random_uint32))
        uin_val = random.randint(0, 0xFFFFFFFF)
        uin_b64 = base64.b64encode(str(uin_val).encode()).decode()
        
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": uin_b64,
        }
        if bot_token:
            headers["Authorization"] = f"Bearer {bot_token}"
        return headers
    
    def get_qrcode(self) -> dict:
        """
        获取登录二维码
        
        Returns:
            {
                "qrcode": "weixin://dl/business/?t=xxx",
                "qrcode_img_content": "data:image/png;base64,..."
            }
        """
        url = f"{self.BASE_URL}/ilink/bot/get_bot_qrcode"
        params = {"bot_type": 3}
        headers = self._make_headers()
        
        resp = self.session.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        
        data = resp.json()
        if data.get("ret") != 0:
            raise RuntimeError(f"获取二维码失败: {data}")
        
        return {
            "qrcode": data["qrcode"],
            "qrcode_img_content": data.get("qrcode_img_content", ""),
        }
    
    def get_qrcode_status(self, qrcode: str) -> dict:
        """
        查询二维码扫码状态
        
        Args:
            qrcode: 步骤1获取的 qrcode 字符串
            
        Returns:
            {
                "status": "waiting" | "confirmed" | "expired",
                "bot_token": "...",  # status=confirmed 时存在
                "baseurl": "..."     # status=confirmed 时存在
            }
        """
        url = f"{self.BASE_URL}/ilink/bot/get_qrcode_status"
        params = {"qrcode": qrcode}
        headers = self._make_headers()
        
        resp = self.session.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        
        data = resp.json()
        if data.get("ret") != 0:
            raise RuntimeError(f"查询状态失败: {data}")
        
        result = {"status": data.get("status", "waiting")}
        if result["status"] == "confirmed":
            result["bot_token"] = data.get("bot_token", "")
            result["baseurl"] = data.get("baseurl", self.BASE_URL)
        
        return result
    
    def wait_for_login(
        self,
        qrcode: str,
        poll_interval: float = 2.0,
        max_wait: float = 300.0,
        callback=None
    ) -> tuple:
        """
        轮询等待用户扫码
        
        Args:
            qrcode: 二维码字符串
            poll_interval: 轮询间隔（秒）
            max_wait: 最大等待时间（秒）
            callback: 状态变更回调函数，接收 (status, data) 参数
            
        Returns:
            (bot_token, baseurl)
            
        Raises:
            TimeoutError: 超时未扫码
            RuntimeError: 二维码过期
        """
        start_time = time.time()
        last_status = None
        
        while time.time() - start_time < max_wait:
            data = self.get_qrcode_status(qrcode)
            status = data["status"]
            
            # 状态变更时回调
            if status != last_status and callback:
                callback(status, data)
            last_status = status
            
            if status == "confirmed":
                return data["bot_token"], data["baseurl"]
            
            if status == "expired":
                raise RuntimeError("二维码已过期，请重新获取")
            
            time.sleep(poll_interval)
        
        raise TimeoutError(f"等待扫码超时（{max_wait}秒）")


def save_token(bot_token: str, token_file: str = "~/.copaw/weixin_bot_token"):
    """保存 Token 到文件"""
    path = Path(token_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bot_token, encoding="utf-8")
    path.chmod(0o600)  # 设置权限为仅所有者可读写
    print(f"✓ Token 已保存到: {path}")


def main():
    """完整流程示例"""
    auth = WeixiniLinkAuth()
    
    # 1. 获取二维码
    print("🔄 正在获取登录二维码...")
    qr_data = auth.get_qrcode()
    qrcode = qr_data["qrcode"]
    
    # 2. 显示二维码
    print("\n" + "="*50)
    print("📱 请使用微信扫描二维码登录")
    print("="*50)
    print(f"\n二维码链接: {qrcode}")
    
    # 如果有图片数据，保存为文件
    img_content = qr_data.get("qrcode_img_content", "")
    if img_content and img_content.startswith("data:image"):
        # 提取 base64 数据
        base64_data = img_content.split(",")[1]
        img_bytes = base64.b64decode(base64_data)
        img_path = Path("/tmp/weixin_qrcode.png")
        img_path.write_bytes(img_bytes)
        print(f"二维码图片已保存: {img_path}")
    
    print("\n⏳ 等待扫码...")
    
    # 3. 轮询等待扫码
    def on_status_change(status, data):
        if status == "waiting":
            print("  等待扫码...")
        elif status == "confirmed":
            print("  ✅ 扫码成功！")
    
    try:
        bot_token, baseurl = auth.wait_for_login(
            qrcode,
            poll_interval=2.0,
            callback=on_status_change
        )
        
        print(f"\n✅ 登录成功！")
        print(f"   Bot Token: {bot_token[:20]}...")
        print(f"   Base URL: {baseurl}")
        
        # 4. 保存 Token
        save_token(bot_token)
        
        print("\n🎉 完成！现在可以使用这个 Token 接收/发送微信消息了。")
        
    except TimeoutError:
        print("\n❌ 超时：二维码在5分钟内未被扫描")
    except RuntimeError as e:
        print(f"\n❌ 错误: {e}")


if __name__ == "__main__":
    main()
```

---

### 使用方式

```bash
# 1. 安装依赖
pip install requests

# 2. 运行脚本
python weixin_auth.py

# 3. 按提示扫码
# 脚本会显示二维码链接，并等待你使用微信扫描

# 4. Token 自动保存
# 扫码成功后，Token 保存到 ~/.copaw/weixin_bot_token
```

---

### 使用获取的 Token

获取到 `bot_token` 后，在后续 API 调用中使用：

```python
import requests
import base64
import random

def make_request(bot_token: str, endpoint: str, data: dict = None):
    """使用 Token 调用 iLink API"""
    
    # 生成请求头
    uin_val = random.randint(0, 0xFFFFFFFF)
    uin_b64 = base64.b64encode(str(uin_val).encode()).decode()
    
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {bot_token}",
        "X-WECHAT-UIN": uin_b64,
    }
    
    url = f"https://ilinkai.weixin.qq.com/{endpoint}"
    
    if data:
        resp = requests.post(url, json=data, headers=headers)
    else:
        resp = requests.get(url, headers=headers)
    
    return resp.json()


# 示例：获取消息列表
bot_token = "eyJhbGciOiJIUzI1NiIs..."  # 从文件读取
result = make_request(bot_token, "ilink/bot/getupdates", {
    "get_updates_buf": "",
    "base_info": {"channel_version": "2.0.1"}
})
print(result)
```

### Token 保存位置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `bot_token_file` | `~/.copaw/weixin_bot_token` | Token 持久化存储路径 |
| `bot_token` | `""` | 直接配置 Token（可选） |

**重要**：Token 文件包含敏感信息，确保文件权限为 `600`：

```bash
chmod 600 ~/.copaw/weixin_bot_token
```

---

## 模型预置配置

### 配置文件位置

模型配置存储在 `~/.copaw.secret/providers/` 目录下：

```
~/.copaw.secret/
└── providers/
    ├── builtin/
    │   ├── modelscope.json      # ModelScope 配置
    │   ├── dashscope.json       # DashScope 配置
    │   ├── openai.json          # OpenAI 配置
    │   └── ...
    └── active_llm.json          # 当前激活的 LLM
```

### 预置模型配置示例

#### 1. ModelScope 配置 (`~/.copaw.secret/providers/builtin/modelscope.json`)

```json
{
  "api_key": "ms-your-modelscope-api-key",
  "base_url": "https://api-inference.modelscope.cn/v1",
  "chat_model": "OpenAIChatModel",
  "extra_models": [],
  "generate_kwargs": {}
}
```

#### 2. DashScope 配置 (`~/.copaw.secret/providers/builtin/dashscope.json`)

```json
{
  "api_key": "sk-your-dashscope-api-key",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "chat_model": "OpenAIChatModel",
  "extra_models": [],
  "generate_kwargs": {}
}
```

#### 3. 激活的 LLM 配置 (`~/.copaw.secret/providers/active_llm.json`)

```json
{
  "provider_id": "dashscope",
  "model": "qwen3-max",
  "generate_kwargs": {
    "max_tokens": 2048,
    "temperature": 0.7
  }
}
```

### 支持的模型提供商

| 提供商 | ID | 默认 Base URL | API Key 前缀 |
|--------|-----|---------------|--------------|
| ModelScope | `modelscope` | `https://api-inference.modelscope.cn/v1` | `ms` |
| DashScope | `dashscope` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `sk` |
| 阿里云百炼 | `aliyun-codingplan` | `https://coding.dashscope.aliyuncs.com/v1` | `sk-sp` |
| OpenAI | `openai` | `https://api.openai.com/v1` | - |
| Azure OpenAI | `azure-openai` | 自定义 | - |
| Anthropic | `anthropic` | `https://api.anthropic.com` | `sk-ant-` |
| MiniMax | `minimax` | `https://api.minimax.io/anthropic` | - |
| Google Gemini | `gemini` | `https://generativelanguage.googleapis.com` | - |
| DeepSeek | `deepseek` | `https://api.deepseek.com` | `sk-` |
| Kimi | `kimi-cn` / `kimi-intl` | 见文档 | - |
| Ollama | `ollama` | `http://localhost:11434` | 无需 |
| LM Studio | `lmstudio` | `http://localhost:1234/v1` | 无需 |

---

## 完整预置配置示例

### 步骤 1：创建工作目录结构

```bash
# 创建工作目录
mkdir -p ~/.copaw/workspaces/default
mkdir -p ~/.copaw.secret/providers/builtin

# 设置权限
chmod 700 ~/.copaw.secret
```

### 步骤 2：创建全局配置 (`~/.copaw/config.json`)

```json
{
  "agents": {
    "active_agent": "default",
    "profiles": {
      "default": {
        "id": "default",
        "name": "微信助手",
        "description": "预置配置的微信 AI 助手",
        "enabled": true
      }
    }
  },
  "last_api": {
    "host": "0.0.0.0",
    "port": 7860
  },
  "show_tool_details": false
}
```

### 步骤 3：创建智能体配置 (`~/.copaw/workspaces/default/agent.json`)

```json
{
  "id": "default",
  "name": "微信助手",
  "description": "预置配置的微信 AI 助手",
  "enabled": true,
  "channels": {
    "weixin": {
      "enabled": true,
      "bot_prefix": "",
      "bot_token": "",
      "bot_token_file": "~/.copaw/weixin_bot_token",
      "base_url": "",
      "media_dir": "~/.copaw/media",
      "dm_policy": "open",
      "group_policy": "open",
      "allow_from": [],
      "deny_message": ""
    },
    "console": {
      "enabled": false
    }
  },
  "heartbeat": {
    "every": "30m",
    "target": "main",
    "activeHours": null
  },
  "running": {
    "max_iters": 50,
    "llm_retry_enabled": true,
    "llm_max_retries": 3,
    "llm_backoff_base": 1.0,
    "llm_backoff_cap": 10.0,
    "max_input_length": 131072
  },
  "language": "zh",
  "user_timezone": "Asia/Shanghai",
  "show_tool_details": false
}
```

### 步骤 4：预置模型配置

创建 `~/.copaw.secret/providers/builtin/dashscope.json`：

```json
{
  "api_key": "sk-your-actual-api-key-here",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "chat_model": "OpenAIChatModel",
  "extra_models": [],
  "generate_kwargs": {}
}
```

创建 `~/.copaw.secret/providers/active_llm.json`：

```json
{
  "provider_id": "dashscope",
  "model": "qwen3-max",
  "generate_kwargs": {
    "max_tokens": 2048,
    "temperature": 0.7
  }
}
```

### 步骤 5：创建智能体人设文件 (`~/.copaw/workspaces/default/AGENTS.md`)

```markdown
# 角色

你是微信 AI 助手，一个智能、友好的对话机器人。

## 职责

1. 回答用户的问题，提供有用的信息
2. 保持礼貌和专业的对话态度
3. 在不确定时，诚实地告知用户

## 约束

- 不要分享敏感信息
- 遵守法律法规
- 尊重用户隐私
```

### 步骤 6：设置文件权限

```bash
# 设置敏感文件权限
chmod 600 ~/.copaw.secret/providers/builtin/*.json
chmod 600 ~/.copaw.secret/providers/active_llm.json
chmod 600 ~/.copaw/weixin_bot_token 2>/dev/null || true
```

---

## Docker 部署方案

### Dockerfile 示例

```dockerfile
FROM python:3.11-slim

# 安装依赖
RUN pip install copaw

# 创建工作目录
ENV COPAW_WORKING_DIR=/app/working
ENV COPAW_SECRET_DIR=/app/working.secret

# 复制预置配置
COPY ./predeploy-config/working /app/working
COPY ./predeploy-config/working.secret /app/working.secret

# 设置权限
RUN chmod -R 700 /app/working.secret && \
    chmod -R 600 /app/working.secret/providers/builtin/*.json && \
    chmod 600 /app/working.secret/providers/active_llm.json

# 暴露端口
EXPOSE 7860

# 启动命令
CMD ["copaw", "app", "--host", "0.0.0.0", "--port", "7860"]
```

### docker-compose.yml 示例

```yaml
version: '3.8'

services:
  copaw:
    build: .
    container_name: copaw-weixin
    ports:
      - "7860:7860"
    environment:
      - COPAW_WORKING_DIR=/app/working
      - COPAW_SECRET_DIR=/app/working.secret
      - COPAW_LOG_LEVEL=info
    volumes:
      # 持久化微信 Token（扫码后会生成）
      - ./data/weixin_bot_token:/app/working/weixin_bot_token
      # 持久化媒体文件
      - ./data/media:/app/working/media
      # 持久化对话历史
      - ./data/chats.json:/app/working/workspaces/default/chats.json
    restart: unless-stopped
```

### 预置配置目录结构

```
predeploy-config/
├── working/
│   ├── config.json
│   ├── weixin_bot_token          # 空文件或预置 Token
│   └── workspaces/
│       └── default/
│           ├── agent.json
│           └── AGENTS.md
└── working.secret/
    └── providers/
        ├── builtin/
        │   └── dashscope.json    # 预置 API Key
        └── active_llm.json
```

---

## 验证部署

### 1. 检查配置加载

```bash
# 查看模型配置
copaw models list

# 预期输出：
# === Providers ===
# --------------------------------------------
#   DashScope (dashscope)
# --------------------------------------------
#   base_url        : https://dashscope.aliyuncs.com/compatible-mode/v1
#   api_key         : sk-****
#   models          :
#     - Qwen3 Max (qwen3-max)
# 
# ════════════════════════════════════════════
#   Active Model Slot
# ════════════════════════════════════════════
#   LLM             : dashscope / qwen3-max
```

### 2. 检查微信渠道配置

```bash
# 查看智能体配置
copaw agent config
```

### 3. 启动应用并测试

```bash
# 启动应用
copaw app

# 打开浏览器访问 http://localhost:7860
# 进入 设置 → 渠道 → 微信个人账号
# 点击"获取登录二维码"，扫码后即可使用
```

### 4. 环境变量覆盖（可选）

如需在启动时覆盖配置：

```bash
# 覆盖工作目录
export COPAW_WORKING_DIR=/custom/path

# 覆盖敏感数据目录
export COPAW_SECRET_DIR=/custom/secret

# 覆盖日志级别
export COPAW_LOG_LEVEL=debug

copaw app
```

---

## 常见问题

### Q: Token 文件权限错误？

```bash
# 修复权限
chmod 600 ~/.copaw/weixin_bot_token
chmod 700 ~/.copaw.secret
chmod -R 600 ~/.copaw.secret/providers/builtin/*.json
```

### Q: 模型配置未生效？

检查 `~/.copaw.secret/providers/active_llm.json` 是否存在且格式正确。

### Q: 如何更换模型？

修改 `~/.copaw.secret/providers/active_llm.json` 中的 `provider_id` 和 `model` 字段，然后重启应用。

### Q: 微信渠道未启动？

检查 `~/.copaw/workspaces/default/agent.json` 中的 `channels.weixin.enabled` 是否为 `true`。

---

## 总结

通过以上步骤，你可以实现：

1. **预置模型配置** - 用户无需手动配置 API Key 和选择模型
2. **微信 Token 持久化** - 扫码一次后 Token 自动保存，重启无需重复扫码
3. **开箱即用** - 部署后直接启动即可使用微信 AI 助手

---

## 高级配置

### 1. 插件（技能）默认配置

#### 技能目录结构
```
~/.copaw/workspaces/{agent_id}/
├── active_skills/          # 已启用的技能
├── customized_skills/      # 自定义技能
└── ...
```

#### 预置默认技能

在 `agent.json` 中配置默认启用的技能：

```json
{
  "id": "default",
  "name": "微信助手",
  "enabled": true,
  "skills": {
    "enabled": ["file_reader", "news", "cron"],
    "auto_install": true
  }
}
```

#### 通过代码预置技能

```python
from copaw.agents.skills_manager import SkillService, sync_skills_to_active
from pathlib import Path

workspace_dir = Path("~/.copaw/workspaces/default")

# 启用指定技能
skill_service = SkillService(workspace_dir)
skill_service.enable_skill("file_reader")
skill_service.enable_skill("news")
skill_service.enable_skill("cron")

# 或者批量同步技能
sync_skills_to_active(
    workspace_dir,
    skill_names={"file_reader", "news", "cron", "xlsx", "pdf"}
)
```

---

### 2. 容器环境检测

#### 自动检测机制

CoPaw 通过以下方式检测容器环境：

1. **环境变量检测**（优先）：检查 `COPAW_RUNNING_IN_CONTAINER=1/true/yes`
2. **Docker 环境文件**：检查 `/.dockerenv` 文件是否存在
3. **Cgroup 检测**：检查 `/proc/1/cgroup` 中是否包含 `docker` 或 `kubepods`

#### 设置容器环境变量

```bash
# Dockerfile 中设置
ENV COPAW_RUNNING_IN_CONTAINER=1

# 或者 docker-compose.yml
environment:
  - COPAW_RUNNING_IN_CONTAINER=1
```

#### 容器中的可写目录

| 目录 | 用途 | 持久化建议 |
|------|------|-----------|
| `~/.copaw/` | 工作目录（配置、对话历史） | **必须挂载卷** |
| `~/.copaw.secret/` | 敏感数据（API Key、Token） | **必须挂载卷** |
| `~/.copaw/media/` | 媒体文件（微信图片等） | 建议挂载卷 |
| `/tmp` | 临时文件 | 无需持久化 |

---

### 3. Python 代码执行环境

#### 内置代码执行工具

CoPaw 通过 `agentscope` 提供 `execute_python_code` 工具：

```python
from agentscope.tool import execute_python_code

# 执行 Python 代码
result = execute_python_code(
    code="print('Hello World')",
    timeout=30
)
```

#### 配置代码执行环境

在 `agent.json` 中启用代码执行工具：

```json
{
  "tools": {
    "enabled": [
      "execute_python_code",
      "execute_shell_command",
      "read_file",
      "write_file"
    ],
    "python": {
      "timeout": 60,
      "allowed_modules": ["numpy", "pandas", "requests"],
      "working_dir": "~/.copaw/workspace"
    }
  }
}
```

#### 容器中的 Python 环境

```dockerfile
# Dockerfile 示例
FROM python:3.11-slim

# 安装常用数据科学库
RUN pip install numpy pandas matplotlib requests beautifulsoup4

# 设置工作目录权限
RUN mkdir -p /app/working && chmod 777 /app/working

ENV COPAW_WORKING_DIR=/app/working
ENV COPAW_RUNNING_IN_CONTAINER=1
```

---

### 4. 技能自动搜索与安装

#### 技能仓库源

CoPaw 支持从以下源自动搜索和安装技能：

| 源 | URL 示例 |
|----|---------|
| ClawHub | `https://clawhub.ai` |
| LobeHub | `https://lobehub.com` |
| SkillsMP | `https://skillsmp.com` |
| GitHub | `https://github.com/anthropics/skills` |
| ModelScope | `https://modelscope.cn/skills/` |

#### 配置技能中心

```python
# 环境变量配置
import os

os.environ["COPAW_SKILLS_HUB_BASE_URL"] = "https://clawhub.ai"
os.environ["COPAW_SKILLS_HUB_HTTP_TIMEOUT"] = "15"
os.environ["COPAW_SKILLS_HUB_HTTP_RETRIES"] = "3"
```

#### 自动搜索和安装技能

```python
from copaw.agents.skills_hub import (
    search_hub_skills,
    install_skill_from_hub
)
from pathlib import Path

# 1. 搜索技能
results = search_hub_skills(query="pdf", limit=10)
for skill in results:
    print(f"{skill.name}: {skill.description}")

# 2. 自动安装技能
workspace_dir = Path("~/.copaw/workspaces/default")

result = install_skill_from_hub(
    workspace_dir=workspace_dir,
    bundle_url="https://github.com/anthropics/skills/tree/main/skills/pdf",
    version="",
    enable=True,      # 安装后立即启用
    overwrite=False   # 不覆盖已存在的技能
)

print(f"安装成功: {result.name}, 已启用: {result.enabled}")
```

#### 智能体自动安装技能示例

```python
# predeploy_skills.py - 预置技能安装脚本
from copaw.agents.skills_hub import install_skill_from_hub
from pathlib import Path

def auto_install_skills(workspace_dir):
    """根据需求自动安装技能"""
    required_skills = [
        ("pdf", "https://github.com/anthropics/skills/tree/main/skills/pdf"),
        ("xlsx", "https://github.com/anthropics/skills/tree/main/skills/xlsx"),
        ("docx", "https://github.com/anthropics/skills/tree/main/skills/docx"),
    ]
    
    installed = []
    for name, url in required_skills:
        try:
            result = install_skill_from_hub(
                workspace_dir=workspace_dir,
                bundle_url=url,
                enable=True
            )
            installed.append(result.name)
            print(f"✓ 已安装: {name}")
        except Exception as e:
            print(f"✗ 安装 {name} 失败: {e}")
    
    return installed

if __name__ == "__main__":
    workspace_dir = Path("~/.copaw/workspaces/default").expanduser()
    auto_install_skills(workspace_dir)
```

---

### 5. 完整高级预置配置示例

```json
// ~/.copaw/workspaces/default/agent.json
{
  "id": "default",
  "name": "智能助手",
  "description": "预置配置的AI助手，支持代码执行和自动技能安装",
  "enabled": true,
  "channels": {
    "weixin": {
      "enabled": true,
      "bot_token_file": "~/.copaw/weixin_bot_token",
      "dm_policy": "open",
      "group_policy": "open"
    }
  },
  "tools": {
    "enabled": [
      "execute_python_code",
      "execute_shell_command",
      "read_file",
      "write_file",
      "browser_use"
    ]
  },
  "skills": {
    "auto_install": true,
    "default_enabled": [
      "file_reader",
      "news",
      "cron"
    ]
  },
  "security": {
    "skill_scan_mode": "warn",
    "file_guard_enabled": true
  }
}
```

---

### 6. 环境变量汇总

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `COPAW_RUNNING_IN_CONTAINER` | 标记运行在容器中 | `false` |
| `COPAW_WORKING_DIR` | 工作目录 | `~/.copaw` |
| `COPAW_SECRET_DIR` | 敏感数据目录 | `~/.copaw.secret` |
| `COPAW_SKILLS_HUB_BASE_URL` | 技能中心地址 | `https://clawhub.ai` |
| `COPAW_SKILLS_HUB_HTTP_TIMEOUT` | 技能请求超时(秒) | `15` |
| `COPAW_SKILL_SCAN_MODE` | 技能扫描模式(block/warn/off) | `warn` |
| `COPAW_LOG_LEVEL` | 日志级别(debug/info/warning/error) | `info` |

---

## 总结

通过以上步骤，你可以实现：

1. **预置模型配置** - 用户无需手动配置 API Key 和选择模型
2. **微信 Token 持久化** - 扫码一次后 Token 自动保存，重启无需重复扫码
3. **插件默认配置** - 预置启用常用技能，实现开箱即用
4. **容器环境适配** - 自动检测容器环境，正确配置可写目录
5. **Python 代码执行** - 启用代码执行能力，处理复杂用户问题
6. **技能自动安装** - 自动搜索和安装技能，动态扩展能力

---

## 纯微信交互方案（无需网页配置）

针对只会打文字的用户，可以预置配置实现**纯微信交互**，用户无需打开任何网页，所有操作通过微信公众号对话完成。

### 核心思路

1. **预置所有配置** - 部署时已完成模型、技能、工具的配置
2. **智能体引导式交互** - 通过 AGENTS.md 人设文件，让 AI 主动引导用户
3. **对话式技能安装** - 用户通过文字指令让 AI 自动搜索和安装技能
4. **扫码即用** - 用户只需扫码登录微信，立即开始对话

---

### 1. 预置配置结构

```
~/.copaw/workspaces/default/
├── agent.json              # 启用所有必要工具和技能
├── AGENTS.md               # 核心：定义微信交互流程和指令
├── SOUL.md                 # AI 人设（温暖、主动、引导型）
├── PROFILE.md              # 预置 AI 身份
├── active_skills/          # 预置启用的技能
│   ├── file_reader/        # 文件阅读
│   ├── xlsx/               # Excel 处理
│   ├── pdf/                # PDF 处理
│   └── news/               # 新闻查询
└── memory/                 # 记忆目录
```

---

### 2. 预置 agent.json（完整配置）

```json
{
  "id": "default",
  "name": "微信智能助手",
  "description": "预置配置的微信 AI 助手，支持纯对话交互",
  "enabled": true,
  "channels": {
    "weixin": {
      "enabled": true,
      "bot_prefix": "",
      "bot_token": "",
      "bot_token_file": "~/.copaw/weixin_bot_token",
      "base_url": "",
      "media_dir": "~/.copaw/media",
      "dm_policy": "open",
      "group_policy": "open"
    },
    "console": {
      "enabled": false
    }
  },
  "tools": {
    "enabled": [
      "execute_python_code",
      "execute_shell_command",
      "read_file",
      "write_file",
      "edit_file",
      "browser_use"
    ]
  },
  "skills": {
    "auto_install": true,
    "default_enabled": [
      "file_reader",
      "xlsx",
      "pdf",
      "docx",
      "pptx",
      "news",
      "cron"
    ]
  },
  "heartbeat": {
    "every": "30m",
    "target": "main",
    "activeHours": null
  },
  "running": {
    "max_iters": 50,
    "llm_retry_enabled": true,
    "llm_max_retries": 3,
    "max_input_length": 131072
  },
  "language": "zh",
  "user_timezone": "Asia/Shanghai",
  "show_tool_details": false
}
```

---

### 3. 核心：AGENTS.md 微信交互指南

创建 `~/.copaw/workspaces/default/AGENTS.md`：

```markdown
# 微信智能助手 - 交互指南

## 你的角色

你是「小助手」，一个温暖、主动、耐心的微信 AI 助手。你的目标是通过对话帮助用户完成各种任务，**不需要用户打开任何网页**。

## 首次交互流程

当用户第一次添加你时，发送欢迎消息：

> 你好！我是小助手 🤖
> 
> 我可以帮你：
> • 📄 处理文件（Excel、PDF、Word、PPT）
> • 🔍 搜索新闻和资讯
> • ⏰ 设置定时提醒
> • 💻 写代码、查资料
> • 🛠️ 安装更多技能
> 
> 直接发消息给我，就像和朋友聊天一样！
> 
> 需要帮忙时随时说「帮助」

## 用户指令识别

识别以下关键词，主动提供帮助：

| 用户说 | 你的行动 |
|--------|----------|
| "帮助" / "怎么用" | 发送功能列表和示例 |
| "安装技能" / "添加功能" | 询问需要什么功能，然后自动搜索安装 |
| "看文件" / "读Excel" / "PDF" | 提示用户发送文件，然后处理 |
| "新闻" / "资讯" | 询问关注领域，搜索相关新闻 |
| "定时" / "提醒" / "cron" | 引导设置定时任务 |
| "写代码" / "编程" / "Python" | 询问需求，执行代码 |
| "浏览" / "搜索网页" | 询问网址或关键词，使用浏览器 |

## 技能安装流程（对话式）

当用户想安装新技能时：

1. **询问需求**：
   > 你想添加什么功能呢？比如：
   > • 处理图片
   > • 发送邮件
   > • 管理日程
   > • 或者其他...

2. **搜索技能**：
   - 使用技能搜索功能查找相关技能
   - 向用户展示找到的技能选项

3. **确认安装**：
   > 我找到了「图片处理」技能，可以帮你压缩、裁剪图片。
   > 
   > 回复「安装」即可添加这个功能。

4. **执行安装**：
   - 调用 install_skill_from_hub 安装技能
   - 安装成功后告知用户

5. **演示用法**：
   > ✅ 已安装「图片处理」技能！
   > 
   > 现在你可以：
   > • 发送图片让我压缩
   > • 说「裁剪图片」调整尺寸

## 文件处理流程

当用户发送文件或提到文件处理：

1. **确认文件类型**：
   > 收到你的文件了！这是 Excel 表格，我可以：
   > • 查看内容
   > • 统计数据
   > • 生成图表
   > • 转换成 PDF
   > 
   > 你想做什么？

2. **执行处理**：
   - 使用相应技能处理文件
   - 发送处理结果或文件

## 主动建议

根据对话上下文，主动建议可能需要的功能：

- 用户经常发 Excel → "需要我帮你安装数据分析技能吗？"
- 用户经常问时间 → "需要设置定时提醒功能吗？"
- 用户提到编程 → "我可以直接帮你写和运行代码"

## 约束

- **绝不**要求用户打开网页或控制台
- **绝不**要求用户手动编辑配置文件
- 所有配置都通过对话完成
- 保持回复简洁，适合微信阅读
- 使用 emoji 增加亲和力
```

---

### 4. 预置 SOUL.md（人设文件）

创建 `~/.copaw/workspaces/default/SOUL.md`：

```markdown
# 小助手的灵魂

## 核心价值观

1. **主动服务** - 不等用户问，主动发现需求并提供帮助
2. **零门槛** - 假设用户只会打字，所有操作通过对话完成
3. **温暖陪伴** - 像朋友一样聊天，而不是冷冰冰的工具

## 行为准则

- 用「我」自称，不要用「本助手」
- 多用 emoji，让对话轻松
- 回复要简短，微信上看不费力
- 主动引导，不要让用户思考"接下来该说什么"
- 出错时道歉并给出替代方案

## 能力边界

- 可以执行 Python 代码处理数据
- 可以安装新技能扩展功能
- 可以读写文件
- 可以浏览网页
- **不能**访问用户的微信隐私
- **不能**修改系统配置
```

---

### 5. 预置 PROFILE.md（身份文件）

创建 `~/.copaw/workspaces/default/PROFILE.md`：

```markdown
# 身份资料

## 我的身份

- **名字：** 小助手
- **定位：** 微信智能助手
- **风格：** 温暖、主动、耐心、简洁
- **特点：** 擅长通过对话解决问题，不需要用户懂技术

## 用户资料

- **名字：** （等待用户告知）
- **偏好：** （在对话中逐步了解）
- **常用功能：** （根据使用记录积累）
```

---

### 6. 预置技能安装脚本

创建 `~/.copaw/predeploy_skills.py`：

```python
#!/usr/bin/env python3
"""预置安装常用技能，实现开箱即用"""

import sys
from pathlib import Path

# 添加 copaw 到路径
sys.path.insert(0, "/usr/local/lib/python3.11/site-packages")

from copaw.agents.skills_hub import install_skill_from_hub
from copaw.agents.skills_manager import SkillService

def main():
    workspace_dir = Path("~/.copaw/workspaces/default").expanduser()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    
    # 预置技能列表
    skills_to_install = [
        ("file_reader", "https://github.com/anthropics/skills/tree/main/skills/file_reader"),
        ("xlsx", "https://github.com/anthropics/skills/tree/main/skills/xlsx"),
        ("pdf", "https://github.com/anthropics/skills/tree/main/skills/pdf"),
        ("docx", "https://github.com/anthropics/skills/tree/main/skills/docx"),
        ("pptx", "https://github.com/anthropics/skills/tree/main/skills/pptx"),
        ("news", "https://github.com/openclaw/openclaw/tree/main/skills/news"),
    ]
    
    print("🚀 开始预置安装技能...")
    
    for name, url in skills_to_install:
        try:
            result = install_skill_from_hub(
                workspace_dir=workspace_dir,
                bundle_url=url,
                enable=True,
                overwrite=False
            )
            print(f"✅ {name}: 已安装并启用")
        except Exception as e:
            print(f"⚠️  {name}: {e}")
    
    print("\n✨ 预置完成！")
    print("用户扫码登录后即可开始使用")

if __name__ == "__main__":
    main()
```

---

### 7. Dockerfile（完整部署）

```dockerfile
FROM python:3.11-slim

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl python3-pip build-essential \
    chromium chromium-sandbox \
    fonts-wqy-zenhei fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

# 设置环境变量
ENV COPAW_RUNNING_IN_CONTAINER=1
ENV COPAW_WORKING_DIR=/app/working
ENV COPAW_SECRET_DIR=/app/working.secret
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# 安装 Python 依赖
RUN pip install --no-cache-dir copaw numpy pandas matplotlib requests beautifulsoup4

# 创建工作目录
RUN mkdir -p /app/working/workspaces/default \
    /app/working.secret/providers/builtin

# 复制预置配置
COPY predeploy-config/agent.json /app/working/workspaces/default/
COPY predeploy-config/AGENTS.md /app/working/workspaces/default/
COPY predeploy-config/SOUL.md /app/working/workspaces/default/
COPY predeploy-config/PROFILE.md /app/working/workspaces/default/
COPY predeploy-config/providers/ /app/working.secret/providers/

# 运行预置脚本安装技能
COPY predeploy_skills.py /app/
RUN python3 /app/predeploy_skills.py

# 设置权限
RUN chmod -R 700 /app/working.secret && \
    chmod -R 600 /app/working.secret/providers/builtin/*.json

EXPOSE 7860

CMD ["copaw", "app", "--host", "0.0.0.0", "--port", "7860"]
```

---

### 8. 用户使用流程

```
部署者操作：
1. 配置模型 API Key
2. 构建 Docker 镜像
3. 启动容器
4. 获取微信登录二维码链接
5. 将二维码链接发给用户

用户操作：
1. 点击链接，扫码登录微信
2. 在微信中开始对话
3. 按引导使用各项功能
```

---

### 9. 微信交互示例

**场景 1：首次使用**
```
用户: [扫码添加]
小助手: 你好！我是小助手 🤖
      
      我可以帮你：
      • 📄 处理文件（Excel、PDF、Word、PPT）
      • 🔍 搜索新闻和资讯
n      • ⏰ 设置定时提醒
      • 💻 写代码、查资料
      • 🛠️ 安装更多技能
      
      直接发消息给我，就像和朋友聊天一样！

用户: 帮助
小助手: 这里有一些示例：
      
      📊 「帮我分析这个表格」+ 发送 Excel 文件
      📰 「今天有什么科技新闻」
      ⏰ 「每天早上9点提醒我喝水」
      💻 「写个Python脚本统计字数」
      🛠️ 「我想添加图片处理功能」
```

**场景 2：安装新技能**
```
用户: 我想处理图片
小助手: 我可以帮你安装「图片处理」技能，可以压缩、裁剪、转换格式。
      
      回复「安装」即可添加这个功能。

用户: 安装
小助手: [自动安装中...]
      
      ✅ 已安装「图片处理」技能！
      
      现在你可以：
      • 发送图片让我压缩
      • 说「裁剪成 800x600」调整尺寸
      • 说「转成黑白」处理效果

用户: [发送图片]
小助手: 收到！需要我：
      • 压缩大小
      • 裁剪尺寸
      • 添加水印
      • 或者其他的？
```

**场景 3：处理文件**
```
用户: [发送 Excel 文件]
小助手: 收到你的表格了！包含 3 个工作表，共 156 行数据。
      
      我可以帮你：
      • 查看数据统计
      • 生成图表
      • 筛选特定数据
      • 转换成 PDF
      
      想做什么？

用户: 统计一下销售额
小助手: [执行 Python 代码分析]
      
      📊 统计结果：
      • 总销售额：¥1,234,567
      • 平均订单：¥8,520
      • 最高月份：12月（¥156,789）
      • 需要详细的图表吗？
```

---

### 10. 部署脚本

创建 `deploy.sh`：

```bash
#!/bin/bash
set -e

echo "🚀 开始部署微信智能助手..."

# 1. 创建配置目录
mkdir -p predeploy-config/providers

# 2. 提示输入 API Key
echo ""
echo "请配置模型 API Key:"
read -p "DashScope API Key (sk-xxx): " DASHSCOPE_KEY

# 3. 生成配置文件
cat > predeploy-config/providers/dashscope.json << EOF
{
  "api_key": "$DASHSCOPE_KEY",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "chat_model": "OpenAIChatModel",
  "extra_models": [],
  "generate_kwargs": {}
}
EOF

cat > predeploy-config/providers/active_llm.json << EOF
{
  "provider_id": "dashscope",
  "model": "qwen3-max",
  "generate_kwargs": {
    "max_tokens": 2048,
    "temperature": 0.7
  }
}
EOF

# 4. 复制人设文件
cp docs/AGENTS.md predeploy-config/
cp docs/SOUL.md predeploy-config/
cp docs/PROFILE.md predeploy-config/
cp docs/agent.json predeploy-config/

# 5. 构建镜像
echo ""
echo "🔨 构建 Docker 镜像..."
docker build -t copaw-weixin .

# 6. 启动容器
echo ""
echo "🟢 启动服务..."
docker run -d \
  --name copaw-weixin \
  -p 7860:7860 \
  -v copaw-data:/app/working \
  -v copaw-secrets:/app/working.secret \
  copaw-weixin

echo ""
echo "✅ 部署完成！"
echo ""
echo "下一步："
echo "1. 访问 http://localhost:7860 获取微信登录二维码"
echo "2. 扫码登录后，用户即可在微信中使用"
echo ""
```

---

## 总结

通过以上纯微信交互方案，可以实现：

| 能力 | 实现方式 |
|------|----------|
| **扫码即用** | 预置所有配置，用户只需扫码登录 |
| **对话式技能安装** | 用户说「安装图片处理」，AI 自动搜索安装 |
| **文件处理** | 用户直接发送文件，AI 识别处理 |
| **代码执行** | 用户描述需求，AI 写代码并运行 |
| **新闻搜索** | 用户说「科技新闻」，AI 主动搜索 |
| **定时任务** | 对话式设置 cron 任务 |

**用户全程无需：**
- ❌ 打开网页控制台
- ❌ 手动配置 API Key
- ❌ 选择模型
- ❌ 安装技能
- ❌ 编辑任何配置文件

**用户只需要：**
- ✅ 扫码登录微信
- ✅ 像聊天一样发文字消息

如需进一步定制，可参考 [CoPaw 配置文档](./config.zh.md) 和 [渠道配置文档](./channels.zh.md)。
