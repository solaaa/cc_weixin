"""
微信 iLink Bot API 客户端。

封装登录（二维码扫码）、长轮询收消息、发送文本消息等 HTTP 调用。
基于 weixin-bot-api.md 中的 iLink 协议实现。
"""

import json
import logging
import os
import struct
import uuid
import base64
import time
import urllib.request
import urllib.error

log = logging.getLogger("ilink")

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"
CHANNEL_VERSION = "1.0.2"


def _random_wechat_uin():
    """生成 X-WECHAT-UIN: 随机 uint32 → 十进制字符串 → base64"""
    uint32 = struct.unpack(">I", os.urandom(4))[0]
    return base64.b64encode(str(uint32).encode("utf-8")).decode("ascii")


def _build_headers(token=None, body=None):
    """构建请求头。"""
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_wechat_uin(),
    }
    if body is not None:
        headers["Content-Length"] = str(len(json.dumps(body, ensure_ascii=False).encode("utf-8")))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_get(base_url, path):
    """GET 请求。"""
    url = f"{base_url.rstrip('/')}/{path}"
    req = urllib.request.Request(url, headers=_build_headers())
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_post(base_url, endpoint, body, token=None, timeout_sec=15):
    """POST 请求。"""
    url = f"{base_url.rstrip('/')}/{endpoint}"
    payload = {**body, "base_info": {"channel_version": CHANNEL_VERSION}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers=_build_headers(token, payload),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        if "timed out" in str(e).lower():
            return None  # 长轮询超时，正常
        raise


class ILinkClient:
    """iLink Bot API 客户端。"""

    def __init__(self, token_file=".weixin-token.json"):
        self.token_file = token_file
        self.token = None
        self.base_url = DEFAULT_BASE_URL
        self.account_id = None
        self.user_id = None
        self._get_updates_buf = ""

    def load_token(self):
        """尝试加载已保存的 token。返回是否成功。"""
        if not os.path.exists(self.token_file):
            return False
        with open(self.token_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.token = data.get("token")
        self.base_url = data.get("baseUrl", DEFAULT_BASE_URL)
        self.account_id = data.get("accountId")
        self.user_id = data.get("userId")
        return bool(self.token)

    def _save_token(self, token, base_url, account_id, user_id):
        """持久化 token。"""
        data = {
            "token": token,
            "baseUrl": base_url,
            "accountId": account_id,
            "userId": user_id,
            "savedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(self.token_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.token = token
        self.base_url = base_url
        self.account_id = account_id
        self.user_id = user_id

    def login(self):
        """
        扫码登录流程。在终端显示二维码，等待用户扫码确认。

        返回 True 表示登录成功。
        """
        log.info("🔐 开始微信扫码登录...")

        qr_resp = _api_get(DEFAULT_BASE_URL, f"ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}")
        qrcode = qr_resp["qrcode"]
        qrcode_url = qr_resp.get("qrcode_img_content", "")

        log.info("📱 请用微信扫描以下二维码：")
        _render_qr(qrcode_url)

        log.info("⏳ 等待扫码...")
        deadline = time.time() + 5 * 60
        refresh_count = 0
        current_qrcode = qrcode

        while time.time() < deadline:
            status_resp = _api_get(
                DEFAULT_BASE_URL,
                f"ilink/bot/get_qrcode_status?qrcode={urllib.request.quote(current_qrcode)}",
            )
            status = status_resp.get("status", "")

            if status == "wait":
                log.debug(".")
            elif status == "scaned":
                log.info("👀 已扫码，请在微信端确认...")
            elif status == "expired":
                refresh_count += 1
                if refresh_count > 3:
                    log.error("❌ 二维码多次过期，请重新运行")
                    return False
                log.info(f"⏳ 二维码过期，刷新中 ({refresh_count}/3)...")
                new_qr = _api_get(DEFAULT_BASE_URL, f"ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}")
                current_qrcode = new_qr["qrcode"]
                qrcode_url = new_qr.get("qrcode_img_content", "")
                _render_qr(qrcode_url)
            elif status == "confirmed":
                log.info("✅ 登录成功！")
                self._save_token(
                    token=status_resp["bot_token"],
                    base_url=status_resp.get("baseurl", DEFAULT_BASE_URL),
                    account_id=status_resp.get("ilink_bot_id", ""),
                    user_id=status_resp.get("ilink_user_id", ""),
                )
                log.info(f"  Bot ID  : {self.account_id}")
                log.info(f"  Base URL: {self.base_url}")
                log.info(f"  Token 已保存到 {self.token_file}")
                return True

            time.sleep(1)

        log.error("❌ 登录超时")
        return False

    def get_updates(self):
        """
        长轮询获取新消息。

        返回消息列表（可能为空）。阻塞最多 ~38 秒。
        """
        resp = _api_post(
            self.base_url,
            "ilink/bot/getupdates",
            {"get_updates_buf": self._get_updates_buf},
            self.token,
            timeout_sec=38,
        )
        if resp is None:
            return []
        if resp.get("get_updates_buf"):
            self._get_updates_buf = resp["get_updates_buf"]
        return resp.get("msgs") or []

    def send_text(self, to_user_id, text, context_token):
        """发送文本消息。"""
        client_id = f"py-{uuid.uuid4()}"
        return _api_post(
            self.base_url,
            "ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": to_user_id,
                    "client_id": client_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": context_token,
                    "item_list": [
                        {"type": 1, "text_item": {"text": text}},
                    ],
                }
            },
            self.token,
        )

    def send_typing(self, to_user_id, context_token, typing_ticket=None):
        """发送"正在输入"状态。"""
        body = {
            "to_user_id": to_user_id,
            "context_token": context_token,
        }
        if typing_ticket:
            body["typing_ticket"] = typing_ticket
        try:
            _api_post(self.base_url, "ilink/bot/sendtyping", body, self.token)
        except Exception:
            pass  # typing 失败不影响主流程


def extract_text(msg):
    """从消息 item_list 提取纯文本。"""
    for item in msg.get("item_list") or []:
        t = item.get("type")
        if t == 1 and item.get("text_item", {}).get("text"):
            return item["text_item"]["text"]
        if t == 3 and item.get("voice_item", {}).get("text"):
            return f"[语音] {item['voice_item']['text']}"
        if t == 2:
            return "[图片]"
        if t == 4:
            return f"[文件] {item.get('file_item', {}).get('file_name', '')}"
        if t == 5:
            return "[视频]"
    return "[空消息]"


def _render_qr(url):
    """在终端渲染二维码。"""
    try:
        import qrcode as qr_lib
        qr = qr_lib.QRCode(box_size=1, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        log.info(f"  二维码 URL: {url}")
        log.info("  (安装 qrcode 库可在终端直接显示二维码: pip install qrcode)")
