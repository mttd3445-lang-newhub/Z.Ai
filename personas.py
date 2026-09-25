"""
bot.py — บอท Discord + Z.ai GLM เวอร์ชัน production-ready
พร้อมเครื่องมือเขียนโค้ด: /snippet /complete /test /review
"""
import asyncio
import re
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands

from ai_client import AIClient, AIClientError
from attachments import read_attachments, format_files_for_prompt
from config import (
    AUTO_RESPOND_CHANNEL_IDS, COMMAND_PREFIX, DISCORD_TOKEN,
    HEALTH_CHECK_ENABLED, MSG_CLEARED, MSG_ERROR, MSG_NO_TOKEN,
    MSG_PONG, OWNER_IDS, STATS_ENABLED, USE_SLASH_COMMANDS,
)
from health import start_health_server, update_status, HealthHandler
from logger import setup_logger
from memory import ConversationMemory
from personas import list_personas, get_persona

log = setup_logger("z-bot")

# ---------- สร้าง instances ----------
try:
    ai = AIClient()
    log.info("✅ AI client สร้างแล้ว (model=%s, base=%s)", ai.model, ai.base_url)
except AIClientError as e:
    log.error("❌ สร้าง AI client ไม่ได้: %s", e)
    print(f"❌ {e}")
    sys.exit(1)

memory = ConversationMemory()

# ---------- Discord intents ----------
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.dm_messages = True

bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    help_command=None,
)


# ---------- ตัวช่วย ----------
def _memory_key(message: discord.Message) -> str:
    if isinstance(message.channel, (discord.DMChannel, discord.GroupChannel)):
        return f"dm:{message.author.id}"
    return f"ch:{message.channel.id}"


async def _call_ai(messages):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: ai.chat(messages))


async def _safe_send(channel, text: str):
    if not text:
        return
    chunk_size = 1900
    if len(text) <= chunk_size:
        await channel.send(text)
        return
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > chunk_size:
            if current:
                chunks.append(current)
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        chunks.append(current)
    final = []
    for c in chunks:
        while len(c) > chunk_size:
            final.append(c[:chunk_size])
            c = c[chunk_size:]
        final.append(c)
    for c in final:
        await channel.send(c)
        await asyncio.sleep(0.2)


def _bump_stat(key: str, n: int = 1):
    if STATS_ENABLED and hasattr(HealthHandler, "bot_status"):
        HealthHandler.bot_status[key] = HealthHandler.bot_status.get(key, 0) + n


# ---------- ระบบสร้างรูปอัตโนมัติ ----------
IMAGE_REQUEST_PATTERNS = [
    r'^วาดรูป\s*(.+)',
    r'^วาดภาพ\s*(.+)',
    r'^วาด\s+(.+?)(?:\s+ให้หน่อย|\s+หน่อย|\s+ให้|\s*$)',
    r'^สร้างรูป\s*(.+)',
    r'^สร้างภาพ\s*(.+)',
    r'^ทำรูป\s*(.+)',
    r'^รูป\s+(.+?)(?:\s+ให้หน่อย|\s*$)',
    r'^generate\s+(?:an?\s+)?(?:image|picture)\s+(?:of\s+)?(.+)',
    r'^draw\s+(?:an?\s+)?(?:image|picture)?\s*(?:of\s+)?(.+)',
    r'^make\s+(?:an?\s+)?(?:image|picture)\s+(?:of\s+)?(.+)',
]


def _detect_image_request(text: str):
    text = text.strip()
    for pattern in IMAGE_REQUEST_PATTERNS:
        m = re.match(pattern, text, re.IGNORECASE)
        if m:
            prompt = m.group(1).strip().rstrip('?.!')
            if len(prompt) >= 2:
                return prompt
    return None


async def _generate_image_response(channel, prompt: str):
    async with channel.typing():
        try:
            loop = asyncio.get_event_loop()
            url = await loop.run_in_executor(
                None, lambda: ai.generate_image(prompt)
            )
            if url:
                await _safe_send(channel, f"🎨 รูป: `{prompt}`\n{url}")
                _bump_stat("ai_calls")
            else:
                await _safe_send(channel, "❌ สร้างรูปไม่สำเร็จ")
                _bump_stat("ai_errors")
        except AIClientError as e:
            _bump_stat("ai_errors")
            await _safe_send(channel, f"❌ สร้างรูปไม่ได้: `{e}`")


# ---------- events ----------
@bot.event
async def on_ready():
    log.info("✅ ล็อกอินแล้วในชื่อ %s (id=%s)", bot.user, bot.user.id)

    if USE_SLASH_COMMANDS:
        try:
            synced = await bot.tree.sync()
            log.info("Slash commands ลงทะเบียน %d คำสั่ง", len(synced))
        except Exception as e:
            log.warning("sync slash commands ไม่ได้: %s", e)

    log.info("📢 Auto-respond channels: %s", AUTO_RESPOND_CHANNEL_IDS or "(ไม่ได้ตั้ง)")

    if HEALTH_CHECK_ENABLED:
        start_health_server()

    update_status(guilds=len(bot.guilds),
                  users=sum(g.member_count or 0 for g in bot.guilds))

    try:
        await bot.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name="แชทเป็นเพื่อน | /help"),
        )
    except Exception:
        pass


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    ctx = await bot.get_context(message)
    if ctx.valid:
        await bot.process_commands(message)
        return

    is_dm = isinstance(message.channel, (discord.DMChannel, discord.GroupChannel))
    is_mentioned = (
        bot.user in message.mentions
        or (message.reference and message.reference.resolved and
            getattr(message.reference.resolved, "id", None) == bot.user.id)
    )
    is_auto = not is_dm and message.channel.id in AUTO_RESPOND_CHANNEL_IDS

    if not (is_dm or is_mentioned or is_auto):
        return

    file_contents, skipped = await read_attachments(message)

    content = message.clean_content.strip()
    bot_name = bot.user.display_name
    if content.startswith(f"@{bot_name}"):
        content = content[len(f"@{bot_name}"):].strip()

    if content and file_contents:
        full_prompt = content + "\n\n" + format_files_for_prompt(file_contents)
    elif file_contents:
        full_prompt = ("วิเคราะห์/อธิบาย/แก้ไขไฟล์ต่อไปนี้ตามที่เหมาะสม:\n\n"
                        + format_files_for_prompt(file_contents))
    else:
        full_prompt = content

    if skipped:
        full_prompt += "\n\n⚠️ ไฟล์ที่ข้าม: " + ", ".join(skipped)

    if not full_prompt:
        return

    # เช็คก่อนว่าเป็นคำขอสร้างรูปไหม
    image_prompt = _detect_image_request(content)
    if image_prompt:
        await _generate_image_response(message.channel, image_prompt)
        return

    if STATS_ENABLED:
        update_status(last_message_at=time.time())
        _bump_stat("messages_processed")

    async with message.channel.typing():
        key = _memory_key(message)
        memory.add_user_message(key, full_prompt)

        try:
            reply = await _call_ai(memory.get_history(key))
        except AIClientError as e:
            log.error("AI error: %s", e)
            _bump_stat("ai_errors")
            await _safe_send(message.channel, f"{MSG_ERROR}\n\n`{e}`")
            return
        except Exception as e:
            log.exception("unexpected error")
            _bump_stat("ai_errors")
            await _safe_send(message.channel, MSG_ERROR)
            return

        memory.add_assistant_message(key, reply)
        _bump_stat("ai_calls")

    try:
        await _safe_send(message.channel, reply)
    except discord.HTTPException as e:
        log.error("discord send error: %s", e)


# ---------- prefix commands ----------
@bot.command(name="ping")
async def ping_prefix(ctx: commands.Context):
    await ctx.send(MSG_PONG)


@bot.command(name="clear")
async def clear_prefix(ctx: commands.Context):
    key = f"dm:{ctx.author.id}" if isinstance(ctx.channel, (
        discord.DMChannel, discord.GroupChannel)) else f"ch:{ctx.channel.id}"
    memory.clear(key)
    await ctx.send(MSG_CLEARED)


@bot.command(name="help")
async def help_prefix(ctx: commands.Context):
    await ctx.send(_help_text())


@bot.command(name="persona")
async def persona_prefix(ctx: commands.Context, name: str = ""):
    if not name:
        await ctx.send(_persona_list())
        return
    key = f"dm:{ctx.author.id}" if isinstance(ctx.channel, (
        discord.DMChannel, discord.GroupChannel)) else f"ch:{ctx.channel.id}"
    if memory.set_persona(key, name.lower()):
        p = get_persona(name.lower())
        await ctx.send(f"🎭 บุคลิกถูกเปลี่ยนเป็น **{p['name']}**\n> {p['description']}")
    else:
        await ctx.send(f"❌ ไม่มี persona ชื่อ '{name}'")


# ---------- slash commands พื้นฐาน ----------
@bot.tree.command(name="ping", description="ตรวจสอบว่าบอทยังทำงานอยู่")
async def ping_slash(interaction: discord.Interaction):
    await interaction.response.send_message(MSG_PONG)


@bot.tree.command(name="clear", description="ล้างประวัติการสนทนาในห้องนี้")
async def clear_slash(interaction: discord.Interaction):
    if isinstance(interaction.channel, (discord.DMChannel, discord.GroupChannel)):
        key = f"dm:{interaction.user.id}"
    else:
        key = f"ch:{interaction.channel.id}"
    memory.clear(key)
    await interaction.response.send_message(MSG_CLEARED)


@bot.tree.command(name="help", description="แสดงวิธีใช้บอท")
async def help_slash(interaction: discord.Interaction):
    await interaction.response.send_message(_help_text())


@bot.tree.command(name="persona", description="ดูหรือตั้งบุคลิกของบอท")
@app_commands.describe(name="ชื่อ persona (เว้นว่างเพื่อดูรายการทั้งหมด)")
async def persona_slash(interaction: discord.Interaction, name: str = ""):
    if not name:
        await interaction.response.send_message(_persona_list())
        return
    if isinstance(interaction.channel, (discord.DMChannel, discord.GroupChannel)):
        key = f"dm:{interaction.user.id}"
    else:
        key = f"ch:{interaction.channel.id}"
    if memory.set_persona(key, name.lower()):
        p = get_persona(name.lower())
        await interaction.response.send_message(
            f"🎭 บุคลิกถูกเปลี่ยนเป็น **{p['name']}**\n> {p['description']}"
        )
    else:
        await interaction.response.send_message(f"❌ ไม่มี persona ชื่อ '{name}'")


@bot.tree.command(name="image", description="สร้างรูปจากข้อความ")
@app_commands.describe(prompt="คำอธิบายรูปที่ต้องการสร้าง")
async def image_slash(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer(thinking=True)
    try:
        loop = asyncio.get_event_loop()
        url = await loop.run_in_executor(None, lambda: ai.generate_image(prompt))
        if url:
            await interaction.followup.send(f"🎨 รูปสำหรับ: `{prompt}`\n{url}")
        else:
            await interaction.followup.send("❌ ไม่สามารถสร้างรูปได้")
    except AIClientError as e:
        await interaction.followup.send(f"❌ สร้างรูปไม่สำเร็จ: {e}")


@bot.tree.command(name="stats", description="ดูสถิติการใช้งานบอท")
async def stats_slash(interaction: discord.Interaction):
    s = HealthHandler.bot_status
    uptime = int(time.time() - s.get("started_at", time.time()))
    text = (
        "**📊 สถิติบอท**\n"
        f"⏱ ออนไลน์: {uptime//86400}d {(uptime%86400)//3600}h {(uptime%3600)//60}m\n"
        f"📨 ข้อความประมวลผล: {s.get('messages_processed', 0)}\n"
        f"🤖 การเรียก AI: {s.get('ai_calls', 0)}\n"
        f"⚠️ ข้อผิดพลาด: {s.get('ai_errors', 0)}\n"
        f"🏠 เซิร์ฟเวอร์: {s.get('guilds', 0)}\n"
        f"👥 ผู้ใช้รวม: {s.get('users', 0)}\n"
    )
    await interaction.response.send_message(text)


# ---------- 🆕 เครื่องมือเขียนโค้ดใหม่ ----------
@bot.tree.command(name="snippet", description="สร้างโค้ดสั้น ๆ สำหรับ task เฉพาะ")
@app_commands.describe(
    language="ภาษาโปรแกรม (python/javascript/typescript/sql/go/rust)",
    task="อะไรที่อยากให้โค้ดทำ (เช่น 'หาจำนวนเฉพาะ')"
)
async def snippet_slash(interaction: discord.Interaction, language: str, task: str):
    """สร้างโค้ดสั้น ๆ สำหรับ task เฉพาะ — ใช้ persona coder"""
    await interaction.response.defer(thinking=True)

    snippet_prompt = (
        f"เขียน {language} สำหรับ: {task}\n\n"
        "ข้อกำหนด:\n"
        "- เขียนโค้ดสมบูรณ์ รันได้จริง ไม่มี '...'\n"
        "- มี type hints / type annotations\n"
        "- มี comment อธิบายเป็นไทย\n"
        "- มี error handling\n"
        "- มี example usage ท้ายโค้ด\n"
        "- ใส่ unit test อย่างน้อย 2 test cases\n"
        "- อธิบายสั้น ๆ ก่อนโค้ด และ edge cases หลังโค้ด"
    )

    messages = [
        {"role": "system", "content": get_persona("coder")["system_prompt"]},
        {"role": "user", "content": snippet_prompt},
    ]

    try:
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(None, lambda: ai.chat(messages))
        _bump_stat("ai_calls")
        await _safe_send(interaction.channel, f"🛠️ **Snippet: {language}** — {task}\n\n{reply}")
        await interaction.followup.send("✅ สร้างโค้ดให้แล้ว — ดูด้านบน")
    except AIClientError as e:
        _bump_stat("ai_errors")
        await interaction.followup.send(f"❌ สร้างไม่สำเร็จ: {e}")


@bot.tree.command(name="complete", description="เติมโค้ดที่ขาดในไฟล์แนบให้สมบูรณ์")
@app_commands.describe(
    language="ภาษาของไฟล์ (python/javascript/typescript/sql)"
)
async def complete_slash(interaction: discord.Interaction, language: str):
    """อ่านไฟล์แนบ และเติมโค้ดที่ขาด (เช่น '...' หรือ TODO)"""
    # อ่านไฟล์แนบจาก message ล่าสุดของ channel
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.DMChannel)):
        await interaction.response.send_message("❌ ใช้ใน text channel เท่านั้น")
        return

    # ดึงข้อความล่าสุด 10 ข้อความ
    history = [m async for m in channel.history(limit=10, oldest_first=False)]
    history.reverse()

    # หาข้อความที่มีไฟล์แนบ
    file_contents, _ = await read_attachments_from_history(history, language)
    if not file_contents:
        await interaction.response.send_message(
            "❌ ไม่เจอไฟล์แนบใน 10 ข้อความล่าสุด — กรุณาแนบไฟล์ก่อน"
        )
        return

    await interaction.response.defer(thinking=True)

    prompt = (
        f"เติมโค้ดที่ขาดในไฟล์ต่อไปนี้ (ภาษา {language}) ให้สมบูรณ์รันได้จริง:\n\n"
        + format_files_for_prompt(file_contents)
        + "\n\nเงื่อนไข:\n"
        "- แทนที่ '...', 'TODO', 'pass', '// your code here' ด้วยโค้ดจริง\n"
        "- อธิบายสั้น ๆ ว่าเติมอะไรไปบ้าง\n"
        "- ถ้าโค้ดเดิมผิด ให้แก้ด้วย พร้อมอธิบาย\n"
        "- ส่งคืนเป็นไฟล์เต็ม ไม่ตัด"
    )

    messages = [
        {"role": "system", "content": get_persona("coder")["system_prompt"]},
        {"role": "user", "content": prompt},
    ]

    try:
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(None, lambda: ai.chat(messages))
        _bump_stat("ai_calls")
        await _safe_send(channel, f"🔧 **Complete ({language})**\n\n{reply}")
        await interaction.followup.send("✅ เติมโค้ดให้แล้ว — ดูด้านบน")
    except AIClientError as e:
        _bump_stat("ai_errors")
        await interaction.followup.send(f"❌ ไม่สำเร็จ: {e}")


@bot.tree.command(name="test", description="สร้าง unit tests ให้ไฟล์แนบ")
@app_commands.describe(
    framework="testing framework (pytest/jest/unittest/googletest)"
)
async def test_slash(interaction: discord.Interaction, framework: str = "pytest"):
    """อ่านไฟล์แนบ และสร้าง unit tests"""
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.DMChannel)):
        await interaction.response.send_message("❌ ใช้ใน text channel เท่านั้น")
        return

    history = [m async for m in channel.history(limit=10, oldest_first=False)]
    history.reverse()
    file_contents, _ = await read_attachments_from_history(history, "")
    if not file_contents:
        await interaction.response.send_message(
            "❌ ไม่เจอไฟล์แนบ — กรุณาแนบไฟล์ก่อน"
        )
        return

    await interaction.response.defer(thinking=True)

    prompt = (
        f"เขียน unit tests ด้วย {framework} สำหรับโค้ดต่อไปนี้:\n\n"
        + format_files_for_prompt(file_contents)
        + "\n\nเงื่อนไข:\n"
        "- ครอบคลุม happy path + edge cases + error cases\n"
        "- อย่างน้อย 5 test cases\n"
        "- ใส่ comment อธิบายเป็นไทยว่าแต่ละ test ตรวจอะไร\n"
        "- ใช้ fixtures/mocks ถ้าจำเป็น\n"
        "- สามารถรันได้จริง"
    )

    messages = [
        {"role": "system", "content": get_persona("coder")["system_prompt"]},
        {"role": "user", "content": prompt},
    ]

    try:
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(None, lambda: ai.chat(messages))
        _bump_stat("ai_calls")
        await _safe_send(channel, f"🧪 **Unit Tests ({framework})**\n\n{reply}")
        await interaction.followup.send("✅ สร้าง tests ให้แล้ว — ดูด้านบน")
    except AIClientError as e:
        _bump_stat("ai_errors")
        await interaction.followup.send(f"❌ ไม่สำเร็จ: {e}")


@bot.tree.command(name="review", description="review ไฟล์แนบ — ตรวจหาบั๊ก + suggest ปรับปรุง")
async def review_slash(interaction: discord.Interaction):
    """อ่านไฟล์แนบล่าสุด และ review"""
    channel = interaction.channel
    if not isinstance(channel, (discord.TextChannel, discord.DMChannel)):
        await interaction.response.send_message("❌ ใช้ใน text channel เท่านั้น")
        return

    history = [m async for m in channel.history(limit=10, oldest_first=False)]
    history.reverse()
    file_contents, _ = await read_attachments_from_history(history, "")
    if not file_contents:
        await interaction.response.send_message(
            "❌ ไม่เจอไฟล์แนบ — กรุณาแนบไฟล์ก่อน"
        )
        return

    await interaction.response.defer(thinking=True)

    prompt = (
        "Review โค้ดต่อไปนี้:\n"
        "- หาบั๊ก / ช่องโหว่ security\n"
        "- ตรวจ best practices (DRY, SOLID, KISS)\n"
        "- ตรวจ performance (Big-O, memory)\n"
        "- ตรวจ error handling\n"
        "- ตรวจ naming / readability\n"
        "- ให้คะแนน /10 พร้อมเหตุผล\n"
        "- สรุปเป็น bullet points ก่อน แล้วตามด้วยโค้ดที่แก้แล้ว\n\n"
        "โค้ด:\n"
        + format_files_for_prompt(file_contents)
    )

    messages = [
        {"role": "system", "content": get_persona("coder")["system_prompt"]},
        {"role": "user", "content": prompt},
    ]

    try:
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(None, lambda: ai.chat(messages))
        _bump_stat("ai_calls")
        await _safe_send(channel, f"🔍 **Code Review**\n\n{reply}")
        await interaction.followup.send("✅ Review เสร็จแล้ว — ดูด้านบน")
    except AIClientError as e:
        _bump_stat("ai_errors")
        await interaction.followup.send(f"❌ ไม่สำเร็จ: {e}")


# ---------- helper สำหรับไฟล์แนบจาก history ----------
async def read_attachments_from_history(messages, language_filter: str):
    """อ่านไฟล์แนบจาก history ของข้อความ"""
    import os
    from config import ALLOWED_FILE_EXTENSIONS, MAX_FILE_SIZE_KB
    from logger import setup_logger
    log = setup_logger("attachments")

    file_contents = []

    for msg in messages:
        if not msg.attachments:
            continue
        for att in msg.attachments:
            name = att.filename.lower()
            ext = os.path.splitext(name)[1]
            if ext not in ALLOWED_FILE_EXTENSIONS:
                continue
            size_kb = att.size / 1024
            if size_kb > MAX_FILE_SIZE_KB:
                continue
            try:
                file_bytes = await att.read()
                try:
                    file_text = file_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    file_text = file_bytes.decode("latin-1", errors="replace")
                if len(file_text) > 10000:
                    file_text = file_text[:10000] + "\n\n... [ไฟล์ถูกตัด]"
                file_contents.append({
                    "filename": att.filename,
                    "language": ext.lstrip("."),
                    "content": file_text,
                    "size_kb": round(size_kb, 1),
                })
            except Exception as e:
                log.error("อ่านไฟล์แนบ %s ไม่ได้: %s", att.filename, e)

    return file_contents, []


# ---------- helper text ----------
def _help_text() -> str:
    return (
        "**🤖 Z Bot — คำสั่งที่ใช้ได้**\n"
        f"`{COMMAND_PREFIX}ping` หรือ `/ping` — ตรวจสถานะบอท\n"
        f"`{COMMAND_PREFIX}clear` หรือ `/clear` — ล้างประวัติการสนทนา\n"
        f"`{COMMAND_PREFIX}persona` หรือ `/persona` — ดู/เปลี่ยนบุคลิก\n"
        f"`{COMMAND_PREFIX}help` หรือ `/help` — แสดงคำสั่งนี้\n"
        "`/image <prompt>` — สร้างรูปจากคำอธิบาย\n"
        "`/stats` — ดูสถิติการใช้งาน\n"
        "\n"
        "**🛠️ เครื่องมือเขียนโค้ด:**\n"
        "`/snippet <lang> <task>` — สร้างโค้ดสั้น ๆ สำหรับ task\n"
        "`/complete <lang>` — เติมโค้ดที่ขาดในไฟล์แนบ\n"
        "`/test [framework]` — สร้าง unit tests ให้ไฟล์แนบ\n"
        "`/review` — review ไฟล์แนบ หาบั๊ก + ปรับปรุง\n"
        "\n"
        "**วิธีคุยกับบอท**\n"
        "• ในเซิร์ฟเวอร์: @mention บอทแล้วพิมพ์ข้อความ\n"
        "• ใน DM: พิมพ์อะไรก็ได้ บอทจะตอบทันที\n"
        "• ในห้อง auto-respond: พิมพ์อะไรก็ได้ บอทจะตอบอัตโนมัติ\n"
        "• พิมพ์ 'วาดรูป X' — บอทจะสร้างรูปให้อัตโนมัติ\n"
    )


def _persona_list() -> str:
    lines = ["**🎭 Persona ทั้งหมด:**\n"]
    for key, name, desc in list_personas():
        lines.append(f"• `{key}` — **{name}**: {desc}")
    lines.append(f"\nใช้ `/persona <key>` เพื่อสลับ เช่น `/persona coder`")
    return "\n".join(lines)


# ---------- main ----------
def main():
    if not DISCORD_TOKEN or DISCORD_TOKEN.startswith("ใส่"):
        print(MSG_NO_TOKEN)
        sys.exit(1)

    log.info("🚀 กำลังเริ่มบอท...")
    log.info("📦 Model: %s | Auto-channels: %d", ai.model, len(AUTO_RESPOND_CHANNEL_IDS))

    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        log.info("หยุดบอทแล้ว (Ctrl+C)")
    except discord.LoginFailure:
        log.error("❌ Token ไม่ถูกต้อง — ตรวจสอบ DISCORD_TOKEN ใน .env")
        sys.exit(2)
    except Exception as e:
        log.exception("❌ บอท crash: %s", e)
        sys.exit(3)


if __name__ == "__main__":
    main()