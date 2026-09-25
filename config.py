"""
config.py — ตั้งค่าและค่าคงที่ของบอท
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ---------- Discord ----------
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
COMMAND_PREFIX: str = os.getenv("COMMAND_PREFIX", "!")
USE_SLASH_COMMANDS: bool = os.getenv("USE_SLASH_COMMANDS", "true").lower() == "true"

# ---------- Z.ai API (GLM) ----------
# คุณต้องขอ API key จาก https://open.bigmodel.cn/
ZAI_API_KEY: str = os.getenv("ZAI_API_KEY", "")
ZAI_BASE_URL: str = os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
ZAI_MODEL: str = os.getenv("ZAI_MODEL", "glm-4-plus")  # หรือ glm-4-flash ถ้าต้องการถูกกว่า

# จำนวนข้อความสูงสุดในประวัติการสนทนาต่อ channel/user
MAX_HISTORY_MESSAGES: int = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))

# เวลา timeout การเรียก AI (วินาที)
AI_TIMEOUT_SEC: int = int(os.getenv("AI_TIMEOUT_SEC", "60"))

# จำนวนครั้ง retry หากเรียก AI ไม่สำเร็จ
AI_MAX_RETRIES: int = int(os.getenv("AI_MAX_RETRIES", "2"))

# ---------- บุคลิกบอท ----------
DEFAULT_SYSTEM_PROMPT: str = (
    "คุณคือ 'Z' ผู้ช่วย AI ใน Discord ที่เป็นกันเอง เข้าถึงง่าย และฉลาดหลักแหลม. "
    "ภาษาหลักที่ใช้ตอบคือภาษาไทย เว้นแต่ผู้ใช้ถามด้วยภาษาอื่น ก็ให้สลับภาษาตามผู้ใช้. "
    "ตอบสั้น กระชับ เข้าใจง่าย ใช้คำสุภาพแบบเพื่อนคุยกัน (เช่น 'ครับ', 'เดี๋ยวจัดให้'). "
    "หากไม่แน่ใจ ให้บอกตรง ๆ ว่าไม่ทราบ อย่าแต่งข้อมูลขึ้นมา. "
    "หากผู้ใช้ขอให้เขียนโค้ด ให้ใส่ code block พร้อมระบุภาษาเสมอ."
)

# ---------- ข้อความสำเร็จ / แจ้งเตือน ----------
MSG_THINKING: str = "🤔 กำลังคิดอยู่ รอแป๊บนะครับ..."
MSG_ERROR: str = "❌ ขอโทษครับ เกิดข้อผิดพลาดในการเรียก AI ลองใหม่อีกครั้งนะครับ"
MSG_CLEARED: str = "🧹 ล้างประวัติการสนทนาในห้องนี้เรียบร้อยแล้วครับ!"
MSG_PONG: str = "🏓 ปิง! บอทออนไลน์อยู่ครับ"
MSG_NO_TOKEN: str = (
    "❌ ยังไม่พบ DISCORD_TOKEN — กรุณาสร้างไฟล์ .env จาก .env.example "
    "แล้วใส่ token ของบอทก่อนรัน"
)
MSG_NO_ZAI_KEY: str = (
    "❌ ยังไม่พบ ZAI_API_KEY — ขอ API key จาก https://open.bigmodel.cn/ "
    "แล้วใส่ใน .env ก่อนรัน"
)
