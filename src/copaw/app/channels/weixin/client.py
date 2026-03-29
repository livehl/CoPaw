# -*- coding: utf-8
"""iLink Bot HTTP client for WeChat (WeChat personal account Bot API).

All iLink API endpoints live under https://ilinkai.weixin.qq.com.
Protocol: HTTP/JSON, no third-party SDK required.

Authentication flow:
1. GET /ilink/bot/get_bot_qrcode?bot_type=3  → qrcode + qrcode_img_content
2. Poll GET /ilink/bot/get_qrcode_status?qrcode=<qrcode> until confirmed
3. Save bot_token + baseurl from the confirmed response
4. Use bearer token for all subsequent requests

Media upload flow:
1. Call getuploadurl to get CDN upload URL and AES key
2. Encrypt media data with AES-128-ECB + PKCS7 padding
3. Upload encrypted data to CDN
4. Use returned CDN URL in sendmessage
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from .utils import (
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    generate_aes_key_b64,
    make_headers,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
_CHANNEL_VERSION = "2.0.1"
# Long-poll hold time is up to 35 seconds (server-controlled)
_GETUPDATES_TIMEOUT = 45.0
_DEFAULT_TIMEOUT = 15.0

# CDN base URL for media upload/download
_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

# Message item types (for item_list in sendmessage)
MESSAGE_ITEM_TYPE_TEXT = 1
MESSAGE_ITEM_TYPE_IMAGE = 2
MESSAGE_ITEM_TYPE_VOICE = 3
MESSAGE_ITEM_TYPE_FILE = 4
MESSAGE_ITEM_TYPE_VIDEO = 5


class ILinkClient:
    """Async HTTP client for the WeChat iLink Bot API.

    Args:
        bot_token: Bearer token obtained after QR code login.
        base_url: iLink API base URL (defaults to ilinkai.weixin.qq.com).
    """

    def __init__(
        self,
        bot_token: str = "",
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self.bot_token = bot_token
        self.base_url = base_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the underlying httpx client."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_GETUPDATES_TIMEOUT),
        )

    async def stop(self) -> None:
        """Close the underlying httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        path = path.lstrip("/")
        return f"{self.base_url}/{path}"

    async def _get(self, path: str, params: Dict[str, Any] = None) -> Any:
        assert self._client is not None, "ILinkClient not started"
        headers = make_headers(self.bot_token)
        resp = await self._client.get(
            self._url(path),
            params=params or {},
            headers=headers,
            timeout=_DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    async def _post(
        self,
        path: str,
        body: Dict[str, Any],
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Any:
        assert self._client is not None, "ILinkClient not started"
        headers = make_headers(self.bot_token)
        resp = await self._client.post(
            self._url(path),
            json=body,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Auth APIs
    # ------------------------------------------------------------------

    async def get_bot_qrcode(self) -> Dict[str, Any]:
        """Fetch login QR code.

        Returns dict with keys:
            qrcode (str): QR code string to poll status.
            qrcode_img_content (str): Base64-encoded PNG image of QR code.
        """
        return await self._get("ilink/bot/get_bot_qrcode", {"bot_type": 3})

    async def get_qrcode_status(self, qrcode: str) -> Dict[str, Any]:
        """Poll QR code scan status.

        Returns dict with keys:
            status (str): "waiting" | "scanned" | "confirmed" | "expired"
            bot_token (str): Bearer token (only when status=="confirmed")
            baseurl (str): API base URL (only when status=="confirmed")
        """
        return await self._get(
            "ilink/bot/get_qrcode_status",
            {"qrcode": qrcode},
        )

    async def wait_for_login(
        self,
        qrcode: str,
        poll_interval: float = 1.5,
        max_wait: float = 300.0,
    ) -> Tuple[str, str]:
        """Block until QR code is confirmed or timeout.

        Args:
            qrcode: QR code string from get_bot_qrcode().
            poll_interval: Seconds between poll attempts.
            max_wait: Maximum seconds to wait.

        Returns:
            Tuple of (bot_token, base_url).

        Raises:
            TimeoutError: If login not confirmed within max_wait.
            RuntimeError: If QR code expired.
        """
        elapsed = 0.0
        while elapsed < max_wait:
            data = await self.get_qrcode_status(qrcode)
            status = data.get("status", "")
            if status == "confirmed":
                token = data.get("bot_token", "")
                base_url = data.get("baseurl", self.base_url)
                return token, base_url
            if status == "expired":
                raise RuntimeError(
                    "WeChat QR code expired, please retry login",
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise TimeoutError(f"WeChat QR code not scanned within {max_wait}s")

    # ------------------------------------------------------------------
    # Messaging APIs
    # ------------------------------------------------------------------

    async def getupdates(self, cursor: str = "") -> Dict[str, Any]:
        """Long-poll for incoming messages (holds up to 35 seconds).

        Args:
            cursor: get_updates_buf from previous response;
                empty on first call.

        Returns:
            Dict with keys:
                ret (int): 0 = success.
                msgs (list): List of WeixinMessage dicts (may be absent).
                get_updates_buf (str): Cursor for next call.
                longpolling_timeout_ms (int): Server-side hold time.
        """
        body: Dict[str, Any] = {
            "get_updates_buf": cursor,
            "base_info": {"channel_version": _CHANNEL_VERSION},
        }
        return await self._post(
            "ilink/bot/getupdates",
            body,
            timeout=_GETUPDATES_TIMEOUT,
        )

    async def sendmessage(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Send a message to a WeChat user.

        Args:
            msg: Message dict. Required fields:
                to_user_id (str): Recipient user ID (xxx@im.wechat).
                message_type (int): 2 = BOT.
                message_state (int): 2 = FINISH.
                context_token (str): Token from inbound message (REQUIRED).
                item_list (list): Content items.

        Returns:
            API response dict.
        """
        return await self._post(
            "ilink/bot/sendmessage",
            {"msg": msg, "base_info": {"channel_version": _CHANNEL_VERSION}},
        )

    async def send_text(
        self,
        to_user_id: str,
        text: str,
        context_token: str,
    ) -> Dict[str, Any]:
        """Convenience: send a plain text message.

        Args:
            to_user_id: Recipient user ID.
            text: Message text.
            context_token: context_token from the inbound message.

        Returns:
            API response dict.
        """
        return await self.sendmessage(
            {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": str(uuid.uuid4()),
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
        )

    async def getconfig(self) -> Dict[str, Any]:
        """Fetch bot config (e.g. typing_ticket).

        Returns:
            API response dict.
        """
        return await self._post("ilink/bot/getconfig", {})

    async def sendtyping(
        self,
        to_user_id: str,
        typing_ticket: str,
    ) -> Dict[str, Any]:
        """Send "typing..." indicator to a user.

        Args:
            to_user_id: Recipient user ID.
            typing_ticket: Ticket from getconfig().

        Returns:
            API response dict.
        """
        return await self._post(
            "ilink/bot/sendtyping",
            {
                "to_user_id": to_user_id,
                "typing_ticket": typing_ticket,
            },
        )

    # ------------------------------------------------------------------
    # Media helpers
    # ------------------------------------------------------------------

    async def download_media(
        self,
        url: str,
        aes_key_b64: str = "",
        encrypt_query_param: str = "",
    ) -> bytes:
        """Download a CDN media file and optionally decrypt it.

        iLink media files are stored on https://novac2c.cdn.weixin.qq.com/c2c.
        The 'url' field in image_item/file_item is a hex media-ID (not HTTP).
        The actual download URL is built from CDN base + encrypt_query_param.

        Args:
            url: CDN HTTP URL, or hex media-ID
                (ignored if encrypt_query_param).
            aes_key_b64: Base64-encoded AES-128 key; if empty, no decryption.
            encrypt_query_param: Query param from media.encrypt_query_param;
                if provided, use CDN base URL + this param to download.

        Returns:
            Decrypted (or raw) file bytes.
        """
        assert self._client is not None, "ILinkClient not started"

        if encrypt_query_param:
            cdn_base = "https://novac2c.cdn.weixin.qq.com/c2c"
            # Note: parameter name is "encrypted_query_param" (with 'd')
            enc = quote(encrypt_query_param, safe="")
            download_url = f"{cdn_base}/download?encrypted_query_param={enc}"
        elif url.startswith("http"):
            download_url = url
        else:
            raise ValueError(
                f"Cannot download media: no valid HTTP URL. "
                f"url={url[:40]!r}, encrypt_query_param empty.",
            )

        resp = await self._client.get(download_url, timeout=60.0)
        resp.raise_for_status()
        data = resp.content
        if aes_key_b64:
            data = aes_ecb_decrypt(data, aes_key_b64)
        return data

    # ------------------------------------------------------------------
    # Media upload APIs
    # ------------------------------------------------------------------

    async def getuploadurl(
        self,
        file_type: str,
        file_size: int,
        file_ext: str = "",
    ) -> Dict[str, Any]:
        """Request CDN upload URL and AES key for media upload.

        Args:
            file_type: One of "image", "file", "voice", "video".
            file_size: Size of the file in bytes (before encryption).
            file_ext: File extension (e.g. "jpg", "png", "mp4", "pdf").

        Returns:
            Dict with keys:
                upload_url: CDN upload URL.
                aes_key: Base64-encoded AES-128 key for encryption.
                media_id: Media identifier (may be used in sendmessage).
        """
        body: Dict[str, Any] = {
            "file_type": file_type,
            "file_size": file_size,
            "base_info": {"channel_version": _CHANNEL_VERSION},
        }
        if file_ext:
            body["file_ext"] = file_ext
        return await self._post("ilink/bot/getuploadurl", body)

    async def upload_media(
        self,
        data: bytes,
        file_type: str,
        file_ext: str = "",
        aes_key_b64: str = "",
    ) -> Dict[str, Any]:
        """Upload media to WeChat CDN with AES encryption.

        Flow:
        1. Get upload URL and AES key from getuploadurl (or use provided key).
        2. Encrypt data with AES-128-ECB.
        3. Upload to CDN.

        Args:
            data: Raw media bytes to upload.
            file_type: One of "image", "file", "voice", "video".
            file_ext: File extension (e.g. "jpg", "png", "mp4", "pdf").
            aes_key_b64: Optional pre-generated AES key; if empty, one will
                be generated internally and returned.

        Returns:
            Dict with keys:
                cdn_url: CDN URL for the uploaded media.
                aes_key: Base64-encoded AES key used for encryption.
                media_id: Media identifier (if provided by API).
        """
        assert self._client is not None, "ILinkClient not started"

        # Step 1: Get upload URL
        upload_info = await self.getuploadurl(file_type, len(data), file_ext)
        upload_url = upload_info.get("upload_url", "")
        if not upload_url:
            raise ValueError(
                f"getuploadurl did not return upload_url: {upload_info}",
            )

        # Use provided key or the one from API response
        aes_key = aes_key_b64 or upload_info.get("aes_key", "")
        if not aes_key:
            aes_key = generate_aes_key_b64()

        # Step 2: Encrypt data
        encrypted_data = aes_ecb_encrypt(data, aes_key)

        # Step 3: Upload to CDN
        # The upload_url might be a full URL or relative path
        if upload_url.startswith("http"):
            full_upload_url = upload_url
        else:
            full_upload_url = f"{_CDN_BASE_URL}/{upload_url.lstrip('/')}"

        resp = await self._client.post(
            full_upload_url,
            content=encrypted_data,
            headers={"Content-Type": "application/octet-stream"},
            timeout=60.0,
        )
        resp.raise_for_status()

        # Parse response - might be JSON or contain the CDN URL
        try:
            result = resp.json()
        except Exception:
            result = {}

        # Extract cdn_url from response or construct it
        cdn_url = result.get("cdn_url", "") or result.get("url", "")
        if not cdn_url:
            # Some responses may return the URL in different fields
            cdn_url = result.get("media_id", "")

        return {
            "cdn_url": cdn_url,
            "aes_key": aes_key,
            "media_id": result.get("media_id", ""),
            "raw_response": result,
        }

    # ------------------------------------------------------------------
    # Send media message helpers
    # ------------------------------------------------------------------

    async def send_image(
        self,
        to_user_id: str,
        image_data: bytes,
        context_token: str,
        file_ext: str = "jpg",
    ) -> Dict[str, Any]:
        """Send an image message.

        Args:
            to_user_id: Recipient user ID.
            image_data: Raw image bytes.
            context_token: context_token from the inbound message.
            file_ext: Image extension (default "jpg").

        Returns:
            API response dict.
        """
        upload_result = await self.upload_media(
            image_data,
            file_type="image",
            file_ext=file_ext,
        )
        return await self._send_media_message(
            to_user_id=to_user_id,
            context_token=context_token,
            item_type=MESSAGE_ITEM_TYPE_IMAGE,
            media_url=upload_result["cdn_url"],
            aes_key=upload_result["aes_key"],
            file_ext=file_ext,
        )

    async def send_file(
        self,
        to_user_id: str,
        file_data: bytes,
        filename: str,
        context_token: str,
    ) -> Dict[str, Any]:
        """Send a file message.

        Args:
            to_user_id: Recipient user ID.
            file_data: Raw file bytes.
            filename: Display filename (used for extension detection).
            context_token: context_token from the inbound message.

        Returns:
            API response dict.
        """
        file_ext = os.path.splitext(filename)[1].lstrip(".") or "bin"
        upload_result = await self.upload_media(
            file_data,
            file_type="file",
            file_ext=file_ext,
        )
        return await self._send_media_message(
            to_user_id=to_user_id,
            context_token=context_token,
            item_type=MESSAGE_ITEM_TYPE_FILE,
            media_url=upload_result["cdn_url"],
            aes_key=upload_result["aes_key"],
            filename=filename,
            file_ext=file_ext,
        )

    async def send_voice(
        self,
        to_user_id: str,
        voice_data: bytes,
        context_token: str,
        file_ext: str = "amr",
    ) -> Dict[str, Any]:
        """Send a voice message.

        Args:
            to_user_id: Recipient user ID.
            voice_data: Raw voice bytes.
            context_token: context_token from the inbound message.
            file_ext: Voice format extension (default "amr").

        Returns:
            API response dict.
        """
        upload_result = await self.upload_media(
            voice_data,
            file_type="voice",
            file_ext=file_ext,
        )
        return await self._send_media_message(
            to_user_id=to_user_id,
            context_token=context_token,
            item_type=MESSAGE_ITEM_TYPE_VOICE,
            media_url=upload_result["cdn_url"],
            aes_key=upload_result["aes_key"],
            file_ext=file_ext,
        )

    async def send_video(
        self,
        to_user_id: str,
        video_data: bytes,
        context_token: str,
        file_ext: str = "mp4",
    ) -> Dict[str, Any]:
        """Send a video message.

        Args:
            to_user_id: Recipient user ID.
            video_data: Raw video bytes.
            context_token: context_token from the inbound message.
            file_ext: Video format extension (default "mp4").

        Returns:
            API response dict.
        """
        upload_result = await self.upload_media(
            video_data,
            file_type="video",
            file_ext=file_ext,
        )
        return await self._send_media_message(
            to_user_id=to_user_id,
            context_token=context_token,
            item_type=MESSAGE_ITEM_TYPE_VIDEO,
            media_url=upload_result["cdn_url"],
            aes_key=upload_result["aes_key"],
            file_ext=file_ext,
        )

    async def _send_media_message(
        self,
        to_user_id: str,
        context_token: str,
        item_type: int,
        media_url: str,
        aes_key: str,
        file_ext: str = "",
        filename: str = "",
    ) -> Dict[str, Any]:
        """Internal helper to send a media message via sendmessage.

        Args:
            to_user_id: Recipient user ID.
            context_token: context_token from inbound message.
            item_type: Message item type (IMAGE=2, VOICE=3, FILE=4, VIDEO=5).
            media_url: CDN URL of the uploaded media.
            aes_key: AES key used for encryption (sent to recipient).
            file_ext: File extension.
            filename: Filename (for file type only).

        Returns:
            API response dict.
        """
        item: Dict[str, Any] = {"type": item_type}

        if item_type == MESSAGE_ITEM_TYPE_IMAGE:
            item["image_item"] = {
                "media": {
                    "url": media_url,
                    "aes_key": aes_key,
                },
            }
        elif item_type == MESSAGE_ITEM_TYPE_VOICE:
            item["voice_item"] = {
                "media": {
                    "url": media_url,
                    "aes_key": aes_key,
                },
            }
        elif item_type == MESSAGE_ITEM_TYPE_FILE:
            item["file_item"] = {
                "media": {
                    "url": media_url,
                    "aes_key": aes_key,
                },
                "filename": filename,
            }
        elif item_type == MESSAGE_ITEM_TYPE_VIDEO:
            item["video_item"] = {
                "media": {
                    "url": media_url,
                    "aes_key": aes_key,
                },
            }

        return await self.sendmessage(
            {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": str(uuid.uuid4()),
                "message_type": 2,  # BOT
                "message_state": 2,  # FINISH
                "context_token": context_token,
                "item_list": [item],
            },
        )
