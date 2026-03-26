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
import tempfile
import urllib.request
import urllib.error
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

log = logging.getLogger("ilink")

CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
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

    def download_image(self, image_item):
        """
        从 CDN 下载图片并 AES-128-ECB 解密，返回本地临时文件路径。

        CDN 下载: {cdnBaseUrl}/download?encrypted_query_param={eqp}
        AES key: image_item.aeskey (hex) 或 image_item.media.aes_key (base64)

        返回 (file_path, media_type) 或 (None, None)。
        """
        log.debug(f"   📷 image_item: {json.dumps(image_item, ensure_ascii=False)[:300]}")

        media_info = image_item.get("media") or {}
        encrypt_query = media_info.get("encrypt_query_param") or ""

        if not encrypt_query:
            log.warning("图片消息缺少 media.encrypt_query_param，跳过下载")
            return None, None

        # 解析 AES key (两种格式)
        aes_key = None
        aes_key_hex = image_item.get("aeskey") or ""
        if aes_key_hex:
            # image_item.aeskey 是 hex 字符串，转为 bytes
            try:
                aes_key = bytes.fromhex(aes_key_hex)
            except ValueError:
                pass
        if aes_key is None and media_info.get("aes_key"):
            aes_key = _parse_aes_key(media_info["aes_key"])

        # 构建 CDN 下载 URL
        cdn_url = f"{CDN_BASE_URL}/download?encrypted_query_param={urllib.request.quote(encrypt_query)}"

        try:
            log.info(f"   📷 正在从 CDN 下载图片...")
            req = urllib.request.Request(cdn_url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw_data = resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")

            log.info(f"   📷 下载完成: {len(raw_data)} bytes")

            if aes_key:
                decrypted_data = _decrypt_aes_ecb(raw_data, aes_key)
                log.info(f"   📷 AES 解密完成: {len(decrypted_data)} bytes")
            else:
                decrypted_data = raw_data

            ext = _guess_image_ext(decrypted_data, content_type)
            tmp = tempfile.NamedTemporaryFile(
                suffix=ext, prefix="wx_img_", delete=False,
                dir=tempfile.gettempdir(),
            )
            tmp.write(decrypted_data)
            tmp.close()

            media_type = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif",
                ".webp": "image/webp",
            }.get(ext, "image/jpeg")

            log.info(f"   📷 图片已保存: {tmp.name}")
            return tmp.name, media_type

        except Exception as e:
            log.error(f"下载图片失败: {e}", exc_info=True)
            return None, None


def _parse_aes_key(aes_key_b64):
    """
    解析 AES key (base64 编码)。

    两种编码格式:
    - base64(raw 16 bytes) → 解码后直接是 16 字节 key
    - base64(hex string of 16 bytes) → 解码后是 32 字符 hex 串，需再次 hex 解码
    """
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        try:
            hex_str = decoded.decode("ascii")
            if all(c in "0123456789abcdefABCDEF" for c in hex_str):
                return bytes.fromhex(hex_str)
        except (UnicodeDecodeError, ValueError):
            pass
    log.warning(f"aes_key 解析失败: 解码后 {len(decoded)} 字节")
    return None


def _decrypt_aes_ecb(data, key):
    """AES-128-ECB 解密（PKCS7 填充）。"""
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    # 尝试去除 PKCS7 填充
    try:
        unpadder = PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        # 某些图片可能不使用标准 PKCS7 填充
        return padded


def _guess_image_ext(data, content_type=""):
    """根据文件头魔数或 Content-Type 推断扩展名。"""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return ".png"
    if data[:2] == b'\xff\xd8':
        return ".jpg"
    if data[:4] == b'GIF8':
        return ".gif"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return ".webp"
    if "png" in content_type:
        return ".png"
    if "gif" in content_type:
        return ".gif"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def extract_text(msg):
    """从消息 item_list 提取纯文本。图片消息也尝试提取同行文本。"""
    texts = []
    has_image = False
    for item in msg.get("item_list") or []:
        t = item.get("type")
        if t == 1 and item.get("text_item", {}).get("text"):
            texts.append(item["text_item"]["text"])
        elif t == 3 and item.get("voice_item", {}).get("text"):
            texts.append(f"[语音] {item['voice_item']['text']}")
        elif t == 2:
            has_image = True
        elif t == 4:
            texts.append(f"[文件] {item.get('file_item', {}).get('file_name', '')}")
        elif t == 5:
            texts.append("[视频]")
    if texts:
        return "\n".join(texts)
    if has_image:
        return "[图片]"
    return "[空消息]"


def extract_images(msg):
    """从消息 item_list 提取图片项列表。返回 image_item 字段的列表。"""
    images = []
    for item in msg.get("item_list") or []:
        if item.get("type") == 2:
            # 优先取 image_item, 备选 pic_item
            image_item = item.get("image_item") or item.get("pic_item")
            if image_item:
                images.append(image_item)
    return images


def get_image_info(file_path):
    """
    获取图片基本信息。返回 (width, height, file_size_bytes) 或 None。
    """
    try:
        from PIL import Image
        file_size = os.path.getsize(file_path)
        with Image.open(file_path) as img:
            return img.width, img.height, file_size
    except Exception as e:
        log.warning(f"获取图片信息失败: {e}")
        return None


def compress_image(file_path, max_long_edge, quality=85):
    """
    压缩图片：等比缩放到 max_long_edge，保存为 JPEG。
    返回新文件路径，或失败返回 None。
    """
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            w, h = img.size
            long_edge = max(w, h)
            if long_edge <= max_long_edge:
                return file_path  # 无需压缩

            ratio = max_long_edge / long_edge
            new_w = int(w * ratio)
            new_h = int(h * ratio)

            resized = img.resize((new_w, new_h), Image.LANCZOS)
            # 转换为 RGB（防止 RGBA 无法保存为 JPEG）
            if resized.mode in ("RGBA", "P"):
                resized = resized.convert("RGB")

            out_path = file_path.rsplit(".", 1)[0] + "_compressed.jpg"
            resized.save(out_path, "JPEG", quality=quality)
            log.info(f"   📷 图片已压缩: {w}x{h} → {new_w}x{new_h}, 保存到 {out_path}")
            return out_path
    except Exception as e:
        log.error(f"压缩图片失败: {e}")
        return None


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
