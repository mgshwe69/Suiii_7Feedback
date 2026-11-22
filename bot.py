import os, secrets, datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from supabase import create_client, Client

SUPABASE_URL = "https://dlebzfhvrokhkmpqqjuy.supabase.co"
SUPABASE_SECRET = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRsZWJ6Zmh2cm9raGttcHFxanV5Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5NTMzODI1MiwiZXhwIjoyMTEwOTE0MjUyfQ.Wkg7LmSw4UAHyfSmliz3_MDkmq3JSw2wuOcwsldnyNc..." # 
(sb_publishable_bnY0Sqc7MBkuhXkwic-2Zw_FcmwWwlW)
BOT_TOKEN = "9072241056:AAGsw9jTjnYpsE1ljkjfqpMQjzzAz8XKaOc"
OWNER_ID = "7777899827"

sb: Client = create_client(SUPABASE_URL, SUPABASE_SECRET)

def gen_key():
    return "SUIII-" + secrets.token_hex(4).upper()

def is_authorized(uid):
    if uid == OWNER_ID: return True
    try:
        res = sb.table("authorized_users").select("*").eq("telegram_id", uid).execute()
        return len(res.data) > 0
    except:
        return False

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        msg = (
            f"**ACCESS REQUIRED**\n"
            f"───────────────\n"
            f"Your ID: `{uid}`\n\n"
            f"Send this ID to the owner.\n"
            f"Once approved, send /start"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Generate Key", callback_data="gen_1")],
        [InlineKeyboardButton("📋 My Keys", callback_data="mykeys")]
    ])
    await update.message.reply_text("**MAIN MENU**\nSelect an option below:", parse_mode="Markdown", reply_markup=kb)

async def approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("Only owner can approve.")
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return
    target_id = int(ctx.args[0])
    try:
        sb.table("authorized_users").insert({"telegram_id": target_id}).execute()
        await update.message.reply_text(f"✅ Approved `{target_id}`\nUser can now /start", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error (already approved?): {e}")

async def generate_key(update_or_query, uid, days=1):
    key = gen_key()
    expires = (datetime.datetime.utcnow() + datetime.timedelta(days=days)).isoformat()
    
    try:
        sb.table("license_keys").insert({
            "key_code": key,
            "telegram_id": uid,
            "duration_days": days,
            "expires_at": expires,
            "status": "active",
        }).execute()
    except Exception as e:
        text = f"DB Error: {e}"
        if isinstance(update_or_query, Update):
            await update_or_query.message.reply_text(text)
        else:
            await update_or_query.edit_message_text(text)
        return

    text = (
        f"**KEY GENERATED (Supabase)**\n"
        f"───────────────\n"
        f"`{key}`\n"
        f"───────────────\n"
        f"**Duration** `{days} days`\n"
        f"**Expiry** `{expires[:10]}`\n"
        f"**Devices** `Unlimited`\n"
        f"**Status** `Active`\n\n"
        f"*Tap the key to copy it.*"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Unbind HWID", callback_data=f"unbind_{key}"), InlineKeyboardButton("Ban Key (Turn Off)", callback_data=f"ban_{key}")],
        [InlineKeyboardButton("Extend Expiry", callback_data=f"extend_{key}"), InlineKeyboardButton("Device Limit", callback_data=f"devlimit_{key}")],
        [InlineKeyboardButton("Share Format", callback_data=f"share_{key}"), InlineKeyboardButton("Delete Key", callback_data=f"delete_{key}")],
        [InlineKeyboardButton("< Back to Menu", callback_data="menu_main")]
    ])
    
    if isinstance(update_or_query, Update):
        await update_or_query.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await update_or_query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
