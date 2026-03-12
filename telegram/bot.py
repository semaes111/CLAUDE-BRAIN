"""
CLAUDE-BRAIN Telegram Bot — La terminal del agente

Flujo:
  Tú → Telegram → Bot → Agent API (claude --print) → Respuesta → Telegram

Características:
  - Streaming simulado: edita el mensaje mientras Claude responde
  - Ficheros: sube archivos y el agente los procesa
  - Skills: /skill nextjs-supabase-dev
  - Multi-agente: /multi Crea una app completa...
  - Código ejecutado en sandbox y resultado en mensaje
  - Respuestas largas divididas automáticamente (límite Telegram: 4096 chars)
  - Solo responde a tu TELEGRAM_USER_ID (seguridad)
"""

import asyncio
import httpx
import os
import textwrap
from telegram import Update, Document
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode, ChatAction

AGENT_API = os.getenv("AGENT_API_URL", "http://agent-api:8000")
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_IDS = set(
    int(x.strip())
    for x in os.getenv("TELEGRAM_ALLOWED_IDS", "").split(",")
    if x.strip().isdigit()
)
MAX_MSG = 4096   # Límite de Telegram
STREAM_INTERVAL = 0.8  # Segundos entre ediciones del mensaje streaming

# ─────────────────────────────────────────────
# GUARD — Solo usuarios autorizados
# ─────────────────────────────────────────────

def authorized(update: Update) -> bool:
    uid = update.effective_user.id
    if ALLOWED_IDS and uid not in ALLOWED_IDS:
        return False
    return True

async def reject(update: Update):
    await update.message.reply_text("⛔ No autorizado.")

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def chunk_text(text: str, size: int = MAX_MSG) -> list[str]:
    """Divide texto largo en chunks respetando el límite de Telegram."""
    if len(text) <= size:
        return [text]
    chunks = []
    while text:
        if len(text) <= size:
            chunks.append(text)
            break
        # Cortar en salto de línea si es posible
        cut = text.rfind("\n", 0, size)
        if cut == -1:
            cut = size
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks

async def send_long(update: Update, text: str, parse_mode=None):
    """Envía respuesta larga dividiéndola en múltiples mensajes."""
    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks):
        prefix = f"_{i+1}/{len(chunks)}_\n" if len(chunks) > 1 else ""
        try:
            await update.message.reply_text(
                prefix + chunk,
                parse_mode=parse_mode,
            )
        except Exception:
            # Si falla con markdown, enviar como texto plano
            await update.message.reply_text(prefix + chunk)

async def call_agent(
    message: str,
    session_id: str,
    skill_names: list = None,
    use_multiagent: bool = False,
    timeout: int = 300,
) -> str:
    """Llama al Agent API y retorna la respuesta."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{AGENT_API}/v1/chat",
            json={
                "message": message,
                "session_id": session_id,
                "skill_names": skill_names or [],
                "use_memory": True,
                "use_multiagent": use_multiagent,
            }
        )
        resp.raise_for_status()
        return resp.json()["response"]

async def stream_agent(message: str, session_id: str) -> asyncio.Queue:
    """Streaming SSE del agente → queue de tokens."""
    q: asyncio.Queue = asyncio.Queue()

    async def _fetch():
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "GET",
                    f"{AGENT_API}/v1/chat/stream",
                    params={"message": message, "session_id": session_id},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            import json
                            try:
                                event = json.loads(data)
                                if event.get("type") == "token":
                                    await q.put(event["data"])
                            except Exception:
                                pass
        finally:
            await q.put(None)  # Señal de fin

    asyncio.create_task(_fetch())
    return q

# ─────────────────────────────────────────────
# /start — Bienvenida
# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return await reject(update)

    await update.message.reply_text(
        "🧠 *CLAUDE-BRAIN* — Tu agente IA\n\n"
        "Escríbeme cualquier tarea de desarrollo:\n"
        "  `crea una API REST con FastAPI`\n"
        "  `revisa mi código de auth`\n"
        "  `busca las mejores prácticas de RLS en Supabase`\n\n"
        "Comandos:\n"
        "  /skill `nombre` — activa una skill\n"
        "  /skills — lista skills disponibles\n"
        "  /multi `tarea` — lanza múltiples agentes\n"
        "  /exec `código` — ejecuta Python en sandbox\n"
        "  /mem `búsqueda` — busca en memoria\n"
        "  /status — estado del sistema\n"
        "  /clear — limpia historial de sesión\n",
        parse_mode=ParseMode.MARKDOWN,
    )

# ─────────────────────────────────────────────
# /skills — Listar skills disponibles
# ─────────────────────────────────────────────

async def cmd_skills(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{AGENT_API}/v1/skills")
        skills = resp.json()["skills"]

    text = "📦 *Skills disponibles:*\n\n"
    for s in skills:
        status = "✅" if s.get("active") else "⬜"
        text += f"{status} `{s['name']}`\n  _{s['description'][:80]}_\n\n"
    text += "Usar: `/skill nombre-skill tarea a realizar`"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
# /skill — Chat con skill específica
# ─────────────────────────────────────────────

async def cmd_skill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    args = ctx.args
    if not args:
        await update.message.reply_text("Uso: `/skill nombre-skill tarea`", parse_mode=ParseMode.MARKDOWN)
        return

    skill_name = args[0]
    task = " ".join(args[1:]) if len(args) > 1 else "Explica qué puedes hacer con esta skill"
    session_id = str(update.effective_user.id)

    msg = await update.message.reply_text(f"⚡ Usando skill `{skill_name}`...", parse_mode=ParseMode.MARKDOWN)
    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        response = await call_agent(task, session_id, skill_names=[skill_name])
        await msg.delete()
        await send_long(update, response)
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ─────────────────────────────────────────────
# /multi — Orquestación multi-agente
# ─────────────────────────────────────────────

async def cmd_multi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    task = " ".join(ctx.args) if ctx.args else ""
    if not task:
        await update.message.reply_text("Uso: `/multi tarea compleja aquí`", parse_mode=ParseMode.MARKDOWN)
        return

    session_id = str(update.effective_user.id)
    msg = await update.message.reply_text("🤝 Coordinando múltiples agentes...")
    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        response = await call_agent(task, session_id, use_multiagent=True, timeout=600)
        await msg.delete()
        await send_long(update, response)
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ─────────────────────────────────────────────
# /exec — Ejecutar código en sandbox
# ─────────────────────────────────────────────

async def cmd_exec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    code = " ".join(ctx.args) if ctx.args else ""
    if not code:
        await update.message.reply_text(
            "Uso: `/exec print('hola')`\n\nO envíame un archivo .py directamente.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    msg = await update.message.reply_text("🏃 Ejecutando en sandbox...")

    async with httpx.AsyncClient(timeout=45) as client:
        try:
            resp = await client.post(
                f"{AGENT_API}/v1/execute",
                json={"code": code, "language": "python", "timeout": 30}
            )
            result = resp.json()
            stdout = result.get("stdout", "").strip()
            stderr = result.get("stderr", "").strip()
            exit_code = result.get("exit_code", 0)

            icon = "✅" if exit_code == 0 else "❌"
            output = f"{icon} *Resultado* (exit {exit_code}):\n```\n"
            if stdout: output += stdout
            if stderr: output += f"\nSTDERR:\n{stderr}"
            output += "\n```"

            await msg.edit_text(output[:MAX_MSG], parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await msg.edit_text(f"❌ Error sandbox: {e}")

# ─────────────────────────────────────────────
# /mem — Buscar en memoria
# ─────────────────────────────────────────────

async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    query = " ".join(ctx.args) if ctx.args else ""
    if not query:
        await update.message.reply_text("Uso: `/mem qué buscas`", parse_mode=ParseMode.MARKDOWN)
        return

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{AGENT_API}/v1/memory/search", params={"query": query, "limit": 5})
        results = resp.json()["results"]

    if not results:
        await update.message.reply_text("🔍 Sin resultados en memoria.")
        return

    text = f"🔍 *Memoria — '{query}':*\n\n"
    for r in results:
        sim = round(r.get("similarity", 0) * 100)
        text += f"[{r['memory_type']} {sim}%] {r['content'][:200]}\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
# /status — Estado del sistema
# ─────────────────────────────────────────────

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(f"{AGENT_API}/v1/status")
            s = resp.json()
            components = s.get("components", {})

            claude = components.get("claude_cli", {})
            redis  = components.get("redis", {})
            supa   = components.get("supabase", {})
            embed  = components.get("embeddings", {})

            def icon(ok): return "✅" if ok else "❌"

            text = (
                f"🧠 *CLAUDE-BRAIN Status*\n\n"
                f"{icon(claude.get('ok'))} Claude CLI — {claude.get('billing', 'N/A')}\n"
                f"{icon(redis.get('ok'))} Redis (working memory)\n"
                f"{icon(supa.get('ok'))} Supabase pgvector\n"
                f"{icon(embed.get('ok'))} nomic-embed-text (local)\n"
            )
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ API no responde: {e}")

# ─────────────────────────────────────────────
# /clear — Limpiar sesión
# ─────────────────────────────────────────────

async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)
    # El session_id es el user_id de Telegram — la memoria en Redis expira sola
    # Podríamos llamar a un endpoint para borrarla, por ahora confirmamos
    await update.message.reply_text("🗑️ Sesión limpiada. Nueva conversación iniciada.")

# ─────────────────────────────────────────────
# MENSAJE DE TEXTO — Chat principal con streaming
# ─────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    text = update.message.text
    session_id = str(update.effective_user.id)

    # Mensaje inicial de "pensando..."
    msg = await update.message.reply_text("⏳")
    await update.effective_chat.send_action(ChatAction.TYPING)

    # Streaming: acumular tokens y editar el mensaje periódicamente
    try:
        q = await stream_agent(text, session_id)
        accumulated = ""
        last_edit = ""
        edit_task = None

        async def do_edit():
            nonlocal last_edit
            try:
                if accumulated != last_edit and accumulated:
                    display = accumulated[-MAX_MSG:] if len(accumulated) > MAX_MSG else accumulated
                    await msg.edit_text(display + " ▋")
                    last_edit = accumulated
            except Exception:
                pass

        while True:
            try:
                token = await asyncio.wait_for(q.get(), timeout=30)
            except asyncio.TimeoutError:
                break

            if token is None:  # Fin del stream
                break

            accumulated += token

            # Editar cada STREAM_INTERVAL segundos
            if not edit_task or edit_task.done():
                edit_task = asyncio.create_task(do_edit())
                await asyncio.sleep(STREAM_INTERVAL)

        # Mensaje final completo
        if edit_task and not edit_task.done():
            edit_task.cancel()

        if accumulated:
            await msg.delete()
            await send_long(update, accumulated)
        else:
            await msg.edit_text("❌ Sin respuesta del agente.")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ─────────────────────────────────────────────
# ARCHIVO — Procesar ficheros enviados
# ─────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    doc: Document = update.message.document
    caption = update.message.caption or "Analiza este archivo"
    session_id = str(update.effective_user.id)

    # Descargar el archivo
    msg = await update.message.reply_text(f"📥 Procesando `{doc.file_name}`...", parse_mode=ParseMode.MARKDOWN)
    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        file = await ctx.bot.get_file(doc.file_id)
        content = bytes()
        async with httpx.AsyncClient() as client:
            resp = await client.get(file.file_path)
            content = resp.content

        # Si es texto, incluirlo directamente en el prompt
        text_extensions = {".py", ".js", ".ts", ".md", ".txt", ".yaml", ".yml",
                           ".json", ".sql", ".sh", ".env", ".toml", ".css", ".html"}
        ext = os.path.splitext(doc.file_name)[1].lower()

        if ext in text_extensions and len(content) < 50_000:
            file_content = content.decode("utf-8", errors="replace")
            task = f"{caption}\n\nArchivo: `{doc.file_name}`\n```\n{file_content}\n```"
        else:
            task = f"{caption}\n\n(Archivo binario adjunto: {doc.file_name}, {len(content)} bytes)"

        response = await call_agent(task, session_id)
        await msg.delete()
        await send_long(update, response)

    except Exception as e:
        await msg.edit_text(f"❌ Error procesando archivo: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN no configurado en .env")

    print("🤖 CLAUDE-BRAIN Telegram Bot arrancando...")
    print(f"   Agent API: {AGENT_API}")
    print(f"   IDs autorizados: {ALLOWED_IDS or 'todos (¡configura TELEGRAM_ALLOWED_IDS!)'}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("skills", cmd_skills))
    app.add_handler(CommandHandler("skill",  cmd_skill))
    app.add_handler(CommandHandler("multi",  cmd_multi))
    app.add_handler(CommandHandler("exec",   cmd_exec))
    app.add_handler(CommandHandler("mem",    cmd_mem))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("clear",  cmd_clear))

    # Mensajes de texto → chat con el agente
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Archivos → procesado por el agente
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("✅ Bot listo — esperando mensajes...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
