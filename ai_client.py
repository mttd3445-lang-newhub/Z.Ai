"""
ai_client.py — เรียก Z.ai GLM API โดยตรงผ่าน HTTP (ไม่ต้องติดตั้ง z-ai CLI)

รองรับ Z.ai (Zhipu AI / GLM) API ซึ่งใช้รูปแบบ OpenAI-compatible
"""
import json
import time
import urllib.request
import urllib.error
from typing import List, Dict

from config import (
    ZAI_API_KEY, ZAI_BASE_URL, ZAI_MODEL,
    AI_TIMEOUT_SEC, AI_MAX_RETRIES,
)


class AIClientError(Exception):
    """ข้อผิดพลาดจากการเรียก Z.ai API"""


class AIClient:
    """Client สำหรับเรียก Z.ai GLM API โดยตรงผ่าน HTTP"""

    def __init__(self, api_key: str = ZAI_API_KEY, base_url: str = ZAI_BASE_URL,
                 model: str = ZAI_MODEL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

        if not self.api_key:
            raise AIClientError(
                "ไม่พบ ZAI_API_KEY — ขอ API key จาก https://open.bigmodel.cn/ "
                "แล้วใส่ใน .env ก่อน"
            )

    def chat(self, messages: List[Dict[str, str]]) -> str:
        """
        ส่งประวัติการสนทนาไปยัง GLM แล้วคืนคำตอบของ assistant

        messages: ลิสต์ของ {role: 'system'|'user'|'assistant', content: str}
        """
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.9,
        }
        body = json.dumps(payload).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_error = None
        for attempt in range(1, AI_MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    url, data=body, headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=AI_TIMEOUT_SEC) as resp:
                    raw = resp.read().decode("utf-8")

                data = json.loads(raw)
                choices = data.get("choices") or []
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    if content:
                        return content

                # บางครั้ง Z.ai ส่ง error กลับมาในรูป 200 พร้อมฟิลด์ error
                if "error" in data:
                    err = data["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise AIClientError(f"API error: {msg}")

                raise AIClientError("การตอบกลับจาก Z.ai ว่างเปล่า")

            except urllib.error.HTTPError as e:
                # อ่าน error body
                try:
                    err_body = e.read().decode("utf-8", errors="replace")
                    err_data = json.loads(err_body)
                    err_msg = err_data.get("error", {}).get("message", err_body)
                except (json.JSONDecodeError, Exception):
                    err_msg = err_body if 'err_body' in locals() else str(e)
                last_error = AIClientError(
                    f"HTTP {e.code}: {err_msg[:300]}"
                )

            except urllib.error.URLError as e:
                last_error = AIClientError(f"network error: {e.reason}")

            except TimeoutError:
                last_error = AIClientError(
                    f"timeout {AI_TIMEOUT_SEC}s"
                )

            except AIClientError as e:
                last_error = e

            # retry ถ้ายังไม่หมดรอบ
            if attempt < AI_MAX_RETRIES:
                time.sleep(1 * attempt)

        raise last_error or AIClientError("ไม่สามารถเรียก Z.ai ได้")
