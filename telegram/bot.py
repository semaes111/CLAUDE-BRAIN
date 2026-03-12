"""
CLAUDE-BRAIN Telegram Bot — Interfaz de lenguaje natural puro

No hay comandos /agent ni /skill.
Escribes lo que necesitas y el sistema elige automáticamente
el agente, skills y comandos correctos de los 119 disponibles.

Únicos comandos utilitarios:
  /exec   → sandbox de código
  /mem    → buscar en memoria
  /forget → borrar tu memoria
  /status → estado del sistema
  /debug  → muestra qué eligió el router (toggle)
"""

import asyncio
import json
import os
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode, ChatAction

AGENT_API    = os.getenv("AGENT_API_URL", "http://agent-api:8000")
BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_IDS  = set(
    int(x.strip()) for x in os.getenv("TELEGRAM_ALLOWED_IDS", "").split(",")
    if x.strip().isdigit()
)
MAX_MSG       = 4096
STREAM_SECS   = 0.8

# Estado por usuario: si debug está ON, muestra el routing decision
_debug_users: set[int] = set()

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def authorized(update: Update) -> bool:
    return not ALLOWED_IDS or update.effective_user.id in ALLOWED_IDS

async def reject(update: Update):
    await update.message.reply_text("⛔ No autorizado.")

def chunk_text(text: str, size: int = MAX_MSG) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    while text:
        if len(text) <= size:
            chunks.append(text); break
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > 0 else size
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks

async def send_long(update: Update, text: str, parse_mode=None):
    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks):
        prefix = f"_{i+1}/{len(chunks)}_\n" if len(chunks) > 1 else ""
        try:
            await update.message.reply_text(prefix + chunk, parse_mode=parse_mode)
        except Exception:
            await update.message.reply_text(prefix + chunk)

# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)
    name = update.effective_user.first_name or "ahí"
    await update.message.reply_text(
        f"🧠 Hola {name}. Soy CLAUDE-BRAIN.\n\n"
        "Escríbeme lo que necesitas en lenguaje natural:\n\n"
        "  _crea una API REST con FastAPI y PostgreSQL_\n"
        "  _revisa la seguridad de este código_\n"
        "  _optimiza esta query SQL_\n"
        "  _busca las mejores prácticas de RLS en Supabase_\n\n"
        "Elijo automáticamente el especialista y las herramientas.\n\n"
        "Comandos utilitarios:\n"
        "  /exec `código` — Python en sandbox seguro\n"
        "  /mem `query` — buscar en tu memoria\n"
        "  /forget — borrar tu memoria\n"
        "  /debug — toggle: ver qué elige el router\n"
        "  /status — estado del sistema",
        parse_mode=ParseMode.MARKDOWN
    )

# ─────────────────────────────────────────────
# /debug — toggle para ver el routing decision
# ─────────────────────────────────────────────

async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)
    uid = update.effective_user.id
    if uid in _debug_users:
        _debug_users.discard(uid)
        await update.message.reply_text("🔕 Debug OFF — respuestas limpias")
    else:
        _debug_users.add(uid)
        await update.message.reply_text(
            "🔍 Debug ON — verás qué agente/skills elige el router en cada respuesta"
        )

# ─────────────────────────────────────────────
# /exec — sandbox de código
# ─────────────────────────────────────────────

async def cmd_exec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)
    code = " ".join(ctx.args) if ctx.args else ""
    if not code:
        await update.message.reply_text(
            "Uso: `/exec print('hola')`\nO envíame directamente un archivo .py",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    msg = await update.message.reply_text("🏃 Ejecutando...")
    async with httpx.AsyncClient(timeout=45) as client:
        try:
            resp = await client.post(f"{AGENT_API}/v1/execute",
                                     json={"code": code, "language": "python", "timeout": 30})
            r = resp.json()
            stdout = r.get("stdout", "").strip()
            stderr = r.get("stderr", "").strip()
            exit_code = r.get("exit_code", 0)
            icon = "✅" if exit_code == 0 else "❌"
            out = f"{icon} exit {exit_code}:\n```\n{stdout or '(sin output)'}"
            if stderr: out += f"\nSTDERR: {stderr}"
            out += "\n```"
            await msg.edit_text(out[:MAX_MSG], parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await msg.edit_text(f"❌ {e}")

# ─────────────────────────────────────────────
# /mem — buscar en memoria
# ─────────────────────────────────────────────

async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)
    query = " ".join(ctx.args) if ctx.args else ""
    if not query:
        await update.message.reply_text("Uso: `/mem qué buscas`", parse_mode=ParseMode.MARKDOWN)
        return
    user_id = str(update.effective_user.id)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{AGENT_API}/v1/memory/search",
                                params={"query": query, "user_id": user_id, "limit": 5})
        results = resp.json().get("results", [])
    if not results:
        await update.message.reply_text("🔍 No encontré nada relevante en tu memoria.")
        return
    text = f"🔍 *Memoria — '{query}':*\n\n"
    for r in results:
        mem_text = r.get("memory", r.get("content", ""))
        score = round(float(r.get("score", 0)) * 100)
        text += f"[{score}%] {mem_text[:200]}\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────
# /forget — borrar toda la memoria del usuario
# ─────────────────────────────────────────────

async def cmd_forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)
    user_id = str(update.effective_user.id)
    msg = await update.message.reply_text("🗑️ Borrando tu memoria...")
    async with httpx.AsyncClient(timeout=15) as client:
        await client.delete(f"{AGENT_API}/v1/memory/user/{user_id}")
    await msg.edit_text("✅ Memoria borrada. Empezamos de cero.")

# ─────────────────────────────────────────────
# /status — estado del sistema
# ─────────────────────────────────────────────

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            s = (await client.get(f"{AGENT_API}/v1/status")).json()
            m = (await client.get(f"{AGENT_API}/v1/watcher/metrics")).json()
            reg = s.get("components", {}).get("registry", {})
            claude = s.get("components", {}).get("claude_cli", {})
            ok = lambda v: "✅" if v else "❌"
            top = "\n".join(f"    `{k}` — {v}x" for k, v in (m.get("top_agents") or {}).items()) or "  _sin datos aún_"
            text = (
                f"🧠 *CLAUDE-BRAIN v2*\n\n"
                f"{ok(claude.get('ok'))} Claude Max OAuth — $0 extra\n\n"
                f"📦 *Registry ({reg.get('total',0)} componentes):*\n"
                f"  🤖 {reg.get('agents',0)} agentes\n"
                f"  🎨 {reg.get('skills',0)} skills\n"
                f"  ⚡ {reg.get('commands',0)} comandos\n\n"
                f"📊 *Actividad:*\n"
                f"  Requests totales: `{m.get('total_requests',0)}`\n"
                f"  Éxito: `{m.get('success_rate',0)}%`\n"
                f"  Latencia media: `{m.get('avg_latency_ms',0)}ms`\n"
                f"  Tokens estimados: `{m.get('tokens_estimated_total',0):,}`\n\n"
                f"🏆 *Agentes más usados:*\n{top}"
            )
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ API no responde: {e}")

# ─────────────────────────────────────────────
# MENSAJE PRINCIPAL — Lenguaje natural puro
# El router elige automáticamente agente/skills/comando
# ─────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    text     = update.message.text
    uid      = update.effective_user.id
    session  = str(uid)
    show_debug = uid in _debug_users

    msg = await update.message.reply_text("⏳")
    await update.effective_chat.send_action(ChatAction.TYPING)

    # Streaming con edición periódica del mensaje
    try:
        accumulated = ""
        last_edit   = ""
        route_info  = None  # se rellena desde la respuesta final

        async def do_edit():
            nonlocal last_edit
            if accumulated != last_edit and accumulated:
                display = accumulated[-MAX_MSG:] if len(accumulated) > MAX_MSG else accumulated
                try:
                    await msg.edit_text(display + " ▋")
                    last_edit = accumulated
                except Exception:
                    pass

        async with httpx.AsyncClient(timeout=310) as client:
            # Usar SSE streaming para la respuesta en tiempo real
            async with client.stream(
                "GET",
                f"{AGENT_API}/v1/chat/stream",
                params={"message": text, "session_id": session, "user_id": session}
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    try:
                        event = json.loads(data)
                        if event.get("type") == "token":
                            accumulated += event["data"]
                            if len(accumulated) % 50 == 0:  # editar cada ~50 chars
                                asyncio.create_task(do_edit())
                                await asyncio.sleep(STREAM_SECS)
                        elif event.get("type") == "done":
                            break
                    except Exception:
                        pass

        # Si el streaming no devolvió nada, llamar al endpoint normal
        if not accumulated:
            r = await httpx.AsyncClient(timeout=310).post(
                f"{AGENT_API}/v1/chat",
                json={"message": text, "session_id": session, "user_id": session,
                      "auto_route": True, "use_memory": True}
            )
            data = r.json()
            accumulated = data.get("response", "Sin respuesta")
            route_info  = data

        # Mensaje final
        await msg.delete()
        await send_long(update, accumulated)

        # Debug: mostrar routing decision si está activado
        if show_debug and route_info:
            agent  = route_info.get("agent_used") or "ninguno"
            skills = ", ".join(route_info.get("skills_used") or []) or "ninguna"
            reason = route_info.get("routing_reasoning", "")
            ms     = route_info.get("latency_ms", 0)
            debug_text = (
                f"🔍 _Router eligió:_\n"
                f"  🤖 Agente: `{agent}`\n"
                f"  🎨 Skills: `{skills}`\n"
                f"  💭 _{reason}_\n"
                f"  ⏱️ `{ms}ms`"
            )
            await update.message.reply_text(debug_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        try:
            await msg.edit_text(f"❌ Error: {e}")
        except Exception:
            await update.message.reply_text(f"❌ Error: {e}")

# ─────────────────────────────────────────────
# ARCHIVOS — El agente los analiza
# ─────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return await reject(update)

    doc     = update.message.document
    caption = update.message.caption or "Analiza este archivo"
    session = str(update.effective_user.id)

    msg = await update.message.reply_text(f"📥 Procesando `{doc.file_name}`...", parse_mode=ParseMode.MARKDOWN)
    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        file = await ctx.bot.get_file(doc.file_id)
        async with httpx.AsyncClient() as client:
            content = (await client.get(file.file_path)).content

        ext = os.path.splitext(doc.file_name)[1].lower()
        text_exts = {".py",".js",".ts",".tsx",".md",".txt",".yaml",".yml",
                     ".json",".sql",".sh",".env",".toml",".css",".html",".jsx"}

        if ext in text_exts and len(content) < 50_000:
            file_content = content.decode("utf-8", errors="replace")
            task = f"{caption}\n\nArchivo: `{doc.file_name}`\n```\n{file_content}\n```"
        else:
            task = f"{caption}\n\n(Archivo: {doc.file_name}, {len(content):,} bytes)"

        async with httpx.AsyncClient(timeout=310) as client:
            r = await client.post(f"{AGENT_API}/v1/chat", json={
                "message": task, "session_id": session, "user_id": session,
                "auto_route": True, "use_memory": True
            })
            response = r.json().get("response", "Sin respuesta")

        await msg.delete()
        await send_long(update, response)

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN no configurado en .env")

    print("🤖 CLAUDE-BRAIN Bot — Lenguaje natural puro")
    print(f"   API: {AGENT_API}")
    print(f"   IDs: {ALLOWED_IDS or 'todos'}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Solo comandos utilitarios — todo lo demás es lenguaje natural
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("debug",  cmd_debug))
    app.add_handler(CommandHandler("exec",   cmd_exec))
    app.add_handler(CommandHandler("mem",    cmd_mem))
    app.add_handler(CommandHandler("forget", cmd_forget))
    app.add_handler(CommandHandler("status", cmd_status))

    # Todo texto → router automático
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("✅ Listo")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
