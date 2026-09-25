"""
memory.py — จัดการประวัติการสนทนาแต่ละห้อง / แต่ละ user
"""
from collections import defaultdict
from typing import Dict, List

from config import DEFAULT_SYSTEM_PROMPT, MAX_HISTORY_MESSAGES


class ConversationMemory:
    """
    เก็บประวัติการสนทนาแยกตาม key (ปกติคือ channel_id หรือ user_id)
    จำกัดจำนวนข้อความเพื่อไม่ให้ context ยาวเกินไป
    """

    def __init__(self, max_messages: int = MAX_HISTORY_MESSAGES,
                 system_prompt: str = DEFAULT_SYSTEM_PROMPT):
        self.max_messages = max_messages
        self.system_prompt = system_prompt
        self._store: Dict[str, List[Dict[str, str]]] = defaultdict(list)

    def get_history(self, key: str) -> List[Dict[str, str]]:
        """คืนประวัติของ key นั้น (รวม system message ที่ตำแหน่งแรก)"""
        history = self._store.get(key, [])
        # ส่งกลับเป็น system prompt ตามด้วยประวัติจริง
        return [{"role": "system", "content": self.system_prompt}] + list(history)

    def add_user_message(self, key: str, content: str) -> None:
        self._store[key].append({"role": "user", "content": content})
        self._trim(key)

    def add_assistant_message(self, key: str, content: str) -> None:
        self._store[key].append({"role": "assistant", "content": content})
        self._trim(key)

    def clear(self, key: str) -> None:
        if key in self._store:
            del self._store[key]

    def clear_all(self) -> None:
        self._store.clear()

    def _trim(self, key: str) -> None:
        """ตัดข้อความเก่าออกถ้าเกินจำนวนสูงสุด (ไม่นับ system ที่อยู่นอก list)"""
        if len(self._store[key]) > self.max_messages:
            # เก็บเพียง N ข้อความสุดท้าย
            self._store[key] = self._store[key][-self.max_messages:]
