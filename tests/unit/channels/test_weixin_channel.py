# -*- coding: utf-8 -*-
"""Unit tests for WeChat iLink Bot channel."""
from __future__ import annotations

import base64
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Direct imports - add src to path first
sys.path.insert(0, '/mnt/CoPaw/src')

# Import utils directly (no agentscope_runtime dependency)
from copaw.app.channels.weixin.utils import (
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    generate_aes_key_b64,
    make_headers,
)

# Import client module and constants
from copaw.app.channels.weixin import client as weixin_client

MESSAGE_ITEM_TYPE_TEXT = weixin_client.MESSAGE_ITEM_TYPE_TEXT
MESSAGE_ITEM_TYPE_IMAGE = weixin_client.MESSAGE_ITEM_TYPE_IMAGE
MESSAGE_ITEM_TYPE_VOICE = weixin_client.MESSAGE_ITEM_TYPE_VOICE
MESSAGE_ITEM_TYPE_FILE = weixin_client.MESSAGE_ITEM_TYPE_FILE
MESSAGE_ITEM_TYPE_VIDEO = weixin_client.MESSAGE_ITEM_TYPE_VIDEO
ILinkClient = weixin_client.ILinkClient


class TestWeixinUtils:
    """Test WeChat iLink utility functions."""

    def test_make_headers_with_token(self):
        """Test header generation with bot token."""
        headers = make_headers("test_token_123")
        assert headers["Content-Type"] == "application/json"
        assert headers["AuthorizationType"] == "ilink_bot_token"
        assert headers["Authorization"] == "Bearer test_token_123"
        assert "X-WECHAT-UIN" in headers

    def test_make_headers_without_token(self):
        """Test header generation without bot token."""
        headers = make_headers("")
        assert headers["Content-Type"] == "application/json"
        assert headers["AuthorizationType"] == "ilink_bot_token"
        assert "Authorization" not in headers
        assert "X-WECHAT-UIN" in headers

    def test_aes_encrypt_decrypt_roundtrip(self):
        """Test AES encryption/decryption roundtrip."""
        # Generate a key
        key_b64 = generate_aes_key_b64()
        assert len(base64.b64decode(key_b64)) == 16

        # Test data
        original_data = b"Hello, WeChat iLink Bot! This is a test message."

        # Encrypt
        encrypted = aes_ecb_encrypt(original_data, key_b64)
        assert encrypted != original_data

        # Decrypt
        decrypted = aes_ecb_decrypt(encrypted, key_b64)
        assert decrypted == original_data

    def test_aes_encrypt_decrypt_binary(self):
        """Test AES encryption/decryption with binary data."""
        key_b64 = generate_aes_key_b64()

        # Binary data (simulating image)
        original_data = bytes([i % 256 for i in range(1000)])

        encrypted = aes_ecb_encrypt(original_data, key_b64)
        decrypted = aes_ecb_decrypt(encrypted, key_b64)

        assert decrypted == original_data

    def test_aes_decrypt_hex_key_format(self):
        """Test AES decryption with hex string key format."""
        # Generate key and convert to hex
        key_bytes = base64.b64decode(generate_aes_key_b64())
        key_hex = key_bytes.hex()  # 32 hex chars

        original_data = b"Test with hex key format"
        encrypted = aes_ecb_encrypt(original_data, base64.b64encode(key_bytes).decode())

        # Decrypt using hex format
        decrypted = aes_ecb_decrypt(encrypted, key_hex)
        assert decrypted == original_data


class TestMessageItemTypes:
    """Test message item type constants."""

    def test_message_type_constants(self):
        """Verify message type constants match iLink protocol."""
        assert MESSAGE_ITEM_TYPE_TEXT == 1
        assert MESSAGE_ITEM_TYPE_IMAGE == 2
        assert MESSAGE_ITEM_TYPE_VOICE == 3
        assert MESSAGE_ITEM_TYPE_FILE == 4
        assert MESSAGE_ITEM_TYPE_VIDEO == 5


class TestILinkClient:
    """Test ILinkClient methods."""

    def test_client_init_default(self):
        """Test client initialization with defaults."""
        client = ILinkClient()
        assert client.bot_token == ""
        assert client.base_url == "https://ilinkai.weixin.qq.com"

    def test_client_init_with_token(self):
        """Test client initialization with token."""
        client = ILinkClient(bot_token="my_token", base_url="https://custom.url/")
        assert client.bot_token == "my_token"
        assert client.base_url == "https://custom.url"

    @pytest.mark.asyncio
    async def test_send_text_message_structure(self):
        """Test send_text builds correct message structure."""
        client = ILinkClient(bot_token="test_token")
        await client.start()

        # Mock the _post method to capture the message
        captured_msg = {}

        async def mock_post(path, body, timeout=15.0):
            captured_msg["path"] = path
            captured_msg["body"] = body
            return {"ret": 0}

        client._post = mock_post

        # Send text
        await client.send_text("user@im.wechat", "Hello", "ctx_token_123")

        # Verify structure
        assert captured_msg["path"] == "ilink/bot/sendmessage"
        msg = captured_msg["body"]["msg"]
        assert msg["to_user_id"] == "user@im.wechat"
        assert msg["message_type"] == 2  # BOT
        assert msg["message_state"] == 2  # FINISH
        assert msg["context_token"] == "ctx_token_123"
        assert len(msg["item_list"]) == 1
        assert msg["item_list"][0]["type"] == MESSAGE_ITEM_TYPE_TEXT

        await client.stop()

    @pytest.mark.asyncio
    async def test_send_image_message_structure(self):
        """Test send_image builds correct message structure."""
        client = ILinkClient(bot_token="test_token")
        await client.start()

        # Mock upload_media and _send_media_message
        async def mock_upload(data, file_type, file_ext="", aes_key_b64=""):
            return {
                "cdn_url": "https://cdn.example.com/image.jpg",
                "aes_key": "dGVzdF9rZXlfMTY=",
                "media_id": "media_123",
            }

        client.upload_media = mock_upload

        captured_msg = {}

        async def mock_post(path, body, timeout=15.0):
            if "sendmessage" in path:
                captured_msg["body"] = body
            return {"ret": 0}

        client._post = mock_post

        # Send image
        image_data = b"fake_image_bytes"
        await client.send_image("user@im.wechat", image_data, "ctx_token", "jpg")

        # Verify image item structure
        msg = captured_msg["body"]["msg"]
        item = msg["item_list"][0]
        assert item["type"] == MESSAGE_ITEM_TYPE_IMAGE
        assert "image_item" in item
        assert item["image_item"]["media"]["url"] == "https://cdn.example.com/image.jpg"
        assert item["image_item"]["media"]["aes_key"] == "dGVzdF9rZXlfMTY="

        await client.stop()

    @pytest.mark.asyncio
    async def test_send_file_message_structure(self):
        """Test send_file builds correct message structure."""
        client = ILinkClient(bot_token="test_token")
        await client.start()

        async def mock_upload(data, file_type, file_ext="", aes_key_b64=""):
            return {
                "cdn_url": "https://cdn.example.com/doc.pdf",
                "aes_key": "dGVzdF9rZXlfMTY=",
                "media_id": "media_456",
            }

        client.upload_media = mock_upload

        captured_msg = {}

        async def mock_post(path, body, timeout=15.0):
            if "sendmessage" in path:
                captured_msg["body"] = body
            return {"ret": 0}

        client._post = mock_post

        # Send file
        file_data = b"fake_file_bytes"
        await client.send_file("user@im.wechat", file_data, "document.pdf", "ctx_token")

        # Verify file item structure
        msg = captured_msg["body"]["msg"]
        item = msg["item_list"][0]
        assert item["type"] == MESSAGE_ITEM_TYPE_FILE
        assert "file_item" in item
        assert item["file_item"]["filename"] == "document.pdf"
        assert item["file_item"]["media"]["url"] == "https://cdn.example.com/doc.pdf"

        await client.stop()

    @pytest.mark.asyncio
    async def test_send_video_message_structure(self):
        """Test send_video builds correct message structure."""
        client = ILinkClient(bot_token="test_token")
        await client.start()

        async def mock_upload(data, file_type, file_ext="", aes_key_b64=""):
            return {
                "cdn_url": "https://cdn.example.com/video.mp4",
                "aes_key": "dGVzdF9rZXlfMTY=",
                "media_id": "media_789",
            }

        client.upload_media = mock_upload

        captured_msg = {}

        async def mock_post(path, body, timeout=15.0):
            if "sendmessage" in path:
                captured_msg["body"] = body
            return {"ret": 0}

        client._post = mock_post

        # Send video
        video_data = b"fake_video_bytes"
        await client.send_video("user@im.wechat", video_data, "ctx_token", "mp4")

        # Verify video item structure
        msg = captured_msg["body"]["msg"]
        item = msg["item_list"][0]
        assert item["type"] == MESSAGE_ITEM_TYPE_VIDEO
        assert "video_item" in item

        await client.stop()

    @pytest.mark.asyncio
    async def test_send_voice_message_structure(self):
        """Test send_voice builds correct message structure."""
        client = ILinkClient(bot_token="test_token")
        await client.start()

        async def mock_upload(data, file_type, file_ext="", aes_key_b64=""):
            return {
                "cdn_url": "https://cdn.example.com/voice.amr",
                "aes_key": "dGVzdF9rZXlfMTY=",
                "media_id": "media_voice",
            }

        client.upload_media = mock_upload

        captured_msg = {}

        async def mock_post(path, body, timeout=15.0):
            if "sendmessage" in path:
                captured_msg["body"] = body
            return {"ret": 0}

        client._post = mock_post

        # Send voice
        voice_data = b"fake_voice_bytes"
        await client.send_voice("user@im.wechat", voice_data, "ctx_token", "amr")

        # Verify voice item structure
        msg = captured_msg["body"]["msg"]
        item = msg["item_list"][0]
        assert item["type"] == MESSAGE_ITEM_TYPE_VOICE
        assert "voice_item" in item

        await client.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
