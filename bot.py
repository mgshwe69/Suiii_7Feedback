from __future__ import annotations

import os
import re
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

# SUIII_CHEATS Ultimate License Manager
# Requires a Cloudflare Worker API implementing the routes listed in API_CONTRACT.md

BOT_TOKEN = "9072241056:AAGsw9jTjnYpsE1ljkjfqpMQjzzAz8XKaOc"
OWNER_ID = 7777899827
WORKER_URL = "http://suiiicheatskey.sultanreew.workers.dev"
BOT_API_SECRET = "SUIII_FB_8xK29mQ7vP4nL6tR9wZ3aB5c"
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
PAGE_SIZE = 8

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
log = logging.getLogger("suiii_keybot")

DURATIONS = [
    ("1 Hour", 60), ("2 Hours", 120), ("5 Hours", 300),
    ("7 Hours", 420), ("12 Hours", 720), ("24 Hours", 1440),
    ("2 Days", 2880), ("4 Days", 5760), ("5 Days", 7200),
    ("7 Days", 10080), ("15 Days", 21600), ("30 Days", 43200),
    ("90 Days", 129600), ("Lifetime", 0),
]


def esc(value: Any) -> str:
    """Escape text for Telegram HTML parse mode."""
    s = str(value if value is not None else "—")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def short(value: Any, limit: int = 34) -> str:
    s = str(value or "—")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def fmt_time(value: Any) -> str:
    if not value:
        return "Never"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return str(value)


class ApiError(Exception):
    pass


class WorkerAPI:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=WORKER_URL,
            timeout=HTTP_TIMEOUT,
            headers={
                "Authorization": f"Bearer {BOT_API_SECRET}",
                "X-Bot-Client": "suiii-telegram-admin",
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, path, **kwargs)
            try:
                payload = response.json()
            except ValueError:
                payload = {"error": response.text[:500]}
            if response.is_error or payload.get("success") is False:
                raise ApiError(str(payload.get("error") or payload.get("message") or f"HTTP {response.status_code}"))
            return payload if isinstance(payload, dict) else {"data": payload}
        except httpx.TimeoutException as exc:
            raise ApiError("Worker request timed out. Please try again.") from exc
        except httpx.HTTPError as exc:
            raise ApiError(f"Worker connection failed: {exc}") from exc

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("POST", path, json=body or {})

    async def patch(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("PATCH", path, json=body or {})

    async def delete(self, path: str) -> dict[str, Any]:
        return await self.request("DELETE", path)


api = WorkerAPI()


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def api_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Server-side authorization is authoritative; local owner check is a fast gate."""
    user = update.effective_user
    if not user:
        return False
    if is_owner(user.id):
        return True
    try:
        result = await api.get("/bot/access", telegram_id=user.id)
        return bool(result.get("authorized") and result.get("role") in {"owner", "admin"})
    except ApiError:
        return False


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await api_admin(update, context):
        return True
    if update.callback_query:
        await update.callback_query.answer("Access denied.", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text("⛔ Access denied. Contact the bot owner.")
    return False


def main_menu(owner: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔑 Generate Key", callback_data="nav:generate"), InlineKeyboardButton("🗂 All Keys", callback_data="keys:0:all")],
        [InlineKeyboardButton("👥 Users", callback_data="nav:users"), InlineKeyboardButton("📊 Analytics", callback_data="nav:stats")],
        [InlineKeyboardButton("📝 Share Templates", callback_data="templates:0"), InlineKeyboardButton("🔎 Search", callback_data="nav:search")],
        [InlineKeyboardButton("🚫 Ban Center", callback_data="nav:bans"), InlineKeyboardButton("📜 Activity Logs", callback_data="logs:0")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="nav:settings"), InlineKeyboardButton("🩺 API Status", callback_data="nav:health")],
    ]
    if owner:
        rows.append([InlineKeyboardButton("🛡 Admin Manager", callback_data="nav:admins"), InlineKeyboardButton("⚠️ Bulk Control", callback_data="nav:bulk")])
        rows.append([InlineKeyboardButton("💾 Backup / Export", callback_data="nav:backup")])
    return InlineKeyboardMarkup(rows)


def back_menu() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("⬅️ Main Menu", callback_data="nav:home")]


async def safe_edit(query, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
    except Exception as exc:
        if "Message is not modified" not in str(exc):
            log.warning("Message edit failed: %s", exc)


async def show_home(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    user = update.effective_user
    owner = bool(user and is_owner(user.id))
    text = (
        "<b>ＳＵＩＩＩ＿ＣＨＥＡＴＳ</b>\n"
        "<b>ULTIMATE LICENSE MANAGER</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔐 <i>Key Generation • License Control • User Management</i>\n\n"
        f"Welcome, <b>{esc(user.first_name if user else 'Admin')}</b>\n"
        f"Role: <b>{'OWNER' if owner else 'ADMIN'}</b>\n\n"
        "Select a control center below."
    )
    markup = main_menu(owner)
    if edit and update.callback_query:
        await safe_edit(update.callback_query, text, markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if not await require_admin(update, context):
        uid = update.effective_user.id if update.effective_user else 0
        await update.effective_message.reply_text(
            f"<b>ACCESS REQUIRED</b>\nYour Telegram ID: <code>{uid}</code>\nSend this ID to the owner for approval.",
            parse_mode=ParseMode.HTML,
        )
        return
    await show_home(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.effective_message.reply_text("Cancelled. Use /start to open the dashboard.")


async def prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, text: str) -> None:
    context.user_data["state"] = state
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def show_stats(query) -> None:
    data = await api.get("/admin/stats")
    s = data.get("stats", data)
    text = (
        "<b>📊 LICENSE ANALYTICS</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🔑 Total Keys: <b>{esc(s.get('total_keys', 0))}</b>\n"
        f"🟢 Active: <b>{esc(s.get('active_keys', 0))}</b>\n"
        f"🟡 Used: <b>{esc(s.get('used_keys', 0))}</b>\n"
        f"⚪ Unused: <b>{esc(s.get('unused_keys', 0))}</b>\n"
        f"⌛ Expired: <b>{esc(s.get('expired_keys', 0))}</b>\n"
        f"🔴 Banned: <b>{esc(s.get('banned_keys', 0))}</b>\n"
        f"👥 Users: <b>{esc(s.get('total_users', 0))}</b>\n"
        f"🛡 Admins: <b>{esc(s.get('total_admins', 0))}</b>\n"
        f"🕒 Updated: <code>{fmt_time(data.get('generated_at'))}</code>"
    )
    await safe_edit(query, text, InlineKeyboardMarkup([back_menu()]))


async def show_generate(query) -> None:
    rows = []
    for i in range(0, len(DURATIONS), 2):
        pair = DURATIONS[i:i + 2]
        rows.append([InlineKeyboardButton(f"{name}", callback_data=f"gen:{mins}") for name, mins in pair])
    rows.extend([
        [InlineKeyboardButton("✍️ Custom Duration", callback_data="gen:custom")],
        [InlineKeyboardButton("📦 Bulk Generate", callback_data="gen:bulk")],
        back_menu(),
    ])
    await safe_edit(query, "<b>🔑 GENERATE LICENSE KEY</b>\n━━━━━━━━━━━━━━━━━━\nChoose a duration. Lifetime is represented by 0 minutes.", InlineKeyboardMarkup(rows))


async def create_key(query, context: ContextTypes.DEFAULT_TYPE, minutes: int, quantity: int = 1) -> None:
    payload = {
        "duration_minutes": minutes,
        "quantity": quantity,
        "telegram_id": query.from_user.id,
        "prefix": context.user_data.get("key_prefix", "SUIII"),
        "device_limit": int(context.user_data.get("device_limit", 1)),
        "activation_mode": context.user_data.get("activation_mode", "first_use"),
        "product": context.user_data.get("product", "default"),
        "internal_note": context.user_data.get("internal_note", ""),
    }
    result = await api.post("/admin/keys/generate", payload)
    keys = result.get("keys") or ([result["key"]] if result.get("key") else [])
    if not keys:
        await safe_edit(query, "<b>Key creation response contained no keys.</b>", InlineKeyboardMarkup([back_menu()]))
        return
    lines = [f"<code>{esc(k.get('key_code') if isinstance(k, dict) else k)}</code>" for k in keys]
    text = (
        "<b>✅ KEY GENERATION COMPLETE</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Generated: <b>{len(keys)}</b>\n"
        f"Duration: <b>{'Lifetime' if minutes == 0 else f'{minutes} minutes'}</b>\n"
        f"Device Limit: <b>{esc(payload['device_limit'])}</b>\n\n" + "\n".join(lines[:50])
    )
    if len(keys) > 50:
        text += f"\n…and {len(keys) - 50} more. Use Export to download the complete batch."
    await safe_edit(query, text, InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Share Format", callback_data=f"sharekey:{esc(keys[0].get('id') if isinstance(keys[0], dict) else keys[0])}")],
        [InlineKeyboardButton("🗂 View Generated Keys", callback_data="keys:0:all")], back_menu(),
    ]))


async def list_keys(query, page: int, status: str = "all") -> None:
    data = await api.get("/admin/keys", page=page, limit=PAGE_SIZE, status=status)
    items = data.get("items", [])
    total_pages = int(data.get("total_pages", 1))
    rows = []
    lines = [f"<b>🗂 KEY MANAGER — {esc(status.upper())}</b>", "━━━━━━━━━━━━━━━━━━"]
    if not items:
        lines.append("No keys found.")
    for item in items:
        key_id = str(item.get("id", ""))
        code = short(item.get("key_code", key_id), 26)
        state = str(item.get("status", "unknown")).upper()
        used = "USED" if item.get("used_by") or item.get("activated_at") else "UNUSED"
        lines.append(f"\n<b>{esc(code)}</b> · <code>{esc(state)}</code> · {used}")
        rows.append([InlineKeyboardButton(f"🔑 {code}", callback_data=f"key:{key_id}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Previous", callback_data=f"keys:{page-1}:{status}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{max(total_pages,1)}", callback_data="noop"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"keys:{page+1}:{status}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("All", callback_data="keys:0:all"), InlineKeyboardButton("Active", callback_data="keys:0:active"), InlineKeyboardButton("Unused", callback_data="keys:0:unused")])
    rows.append([InlineKeyboardButton("Expired", callback_data="keys:0:expired"), InlineKeyboardButton("Banned", callback_data="keys:0:banned")])
    rows.append(back_menu())
    await safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_key(query, key_id: str) -> None:
    data = await api.get(f"/admin/keys/{key_id}")
    k = data.get("key", data)
    status = str(k.get("status", "unknown")).upper()
    used_by = k.get("used_by_username") or k.get("used_by_telegram_id") or "Nobody (unused)"
    text = (
        "<b>🔐 LICENSE DETAILS</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Key: <code>{esc(k.get('key_code'))}</code>\n"
        f"Status: <b>{esc(status)}</b>\n"
        f"Usage: <b>{'USED' if k.get('activated_at') else 'UNUSED'}</b>\n"
        f"Used By: <b>{esc(used_by)}</b>\n"
        f"Telegram ID: <code>{esc(k.get('used_by_telegram_id'))}</code>\n"
        f"Device: <code>{esc(k.get('device_id'))}</code>\n"
        f"Device Limit: <b>{esc(k.get('device_limit', 1))}</b>\n"
        f"Duration: <b>{esc(k.get('duration_minutes', '—'))} minutes</b>\n"
        f"Created: <code>{fmt_time(k.get('created_at'))}</code>\n"
        f"Activated: <code>{fmt_time(k.get('activated_at'))}</code>\n"
        f"Expires: <code>{fmt_time(k.get('expires_at'))}</code>\n"
        f"Last Seen: <code>{fmt_time(k.get('last_seen'))}</code>\n"
        f"Note: {esc(k.get('internal_note'))}"
    )
    rows = [
        [InlineKeyboardButton("🚫 Ban", callback_data=f"act:ban:{key_id}"), InlineKeyboardButton("🟢 Unban", callback_data=f"act:unban:{key_id}")],
        [InlineKeyboardButton("⏸ Disable", callback_data=f"act:disable:{key_id}"), InlineKeyboardButton("▶️ Activate", callback_data=f"act:activate:{key_id}")],
        [InlineKeyboardButton("⏳ Extend Expiry", callback_data=f"act:extend:{key_id}"), InlineKeyboardButton("📱 Device Limit", callback_data=f"act:devices:{key_id}")],
        [InlineKeyboardButton("🔓 Unbind Device", callback_data=f"act:unbind:{key_id}"), InlineKeyboardButton("🔄 Reset Activation", callback_data=f"act:reset:{key_id}")],
        [InlineKeyboardButton("📝 Share Format", callback_data=f"sharekey:{key_id}"), InlineKeyboardButton("🗑 Delete", callback_data=f"confirm:delete:{key_id}")],
        [InlineKeyboardButton("📜 Key History", callback_data=f"history:key:{key_id}")],
        [InlineKeyboardButton("⬅️ Back to Keys", callback_data="keys:0:all"), *back_menu()],
    ]
    await safe_edit(query, text, InlineKeyboardMarkup(rows))


async def show_templates(query, page: int = 0) -> None:
    data = await api.get("/admin/templates", page=page, limit=8)
    templates = data.get("items", [])
    rows = []
    lines = ["<b>📝 SHARE TEMPLATE MANAGER</b>", "━━━━━━━━━━━━━━━━━━"]
    for t in templates:
        tid = str(t.get("id"))
        name = short(t.get("name", "Untitled"), 28)
        default = " ⭐ DEFAULT" if t.get("is_default") else ""
        lines.append(f"\n• <b>{esc(name)}</b>{default} · {'Enabled' if t.get('enabled', True) else 'Disabled'}")
        rows.append([InlineKeyboardButton(f"✏️ {name}", callback_data=f"template:{tid}")])
    if not templates:
        lines.append("No templates yet. Create your first template.")
    rows.append([InlineKeyboardButton("➕ Create Template", callback_data="template:new")])
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="nav:home")])
    await safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_template(query, template_id: str) -> None:
    data = await api.get(f"/admin/templates/{template_id}")
    t = data.get("template", data)
    text = (
        "<b>📝 TEMPLATE DETAILS</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Name: <b>{esc(t.get('name'))}</b>\n"
        f"Default: <b>{'Yes' if t.get('is_default') else 'No'}</b>\n"
        f"Enabled: <b>{'Yes' if t.get('enabled', True) else 'No'}</b>\n\n"
        f"<b>Message:</b>\n<pre>{esc(t.get('body', ''))[:3000]}</pre>\n\n"
        "Variables: {KEY}, {DURATION}, {EXPIRY}, {REMAINING}, {STATUS}, {USERNAME}, {USER_ID}, {DEVICE_LIMIT}, {CUSTOM_MESSAGE}"
    )
    rows = [
        [InlineKeyboardButton("✏️ Edit Name", callback_data=f"templ_edit:name:{template_id}"), InlineKeyboardButton("✏️ Edit Message", callback_data=f"templ_edit:body:{template_id}")],
        [InlineKeyboardButton("⭐ Set Default", callback_data=f"templ_act:default:{template_id}"), InlineKeyboardButton("🔛 Toggle", callback_data=f"templ_act:toggle:{template_id}")],
        [InlineKeyboardButton("👁 Preview", callback_data=f"templ_preview:{template_id}"), InlineKeyboardButton("🗑 Delete", callback_data=f"confirm:templdelete:{template_id}")],
        [InlineKeyboardButton("⬅️ Templates", callback_data="templates:0")],
    ]
    await safe_edit(query, text, InlineKeyboardMarkup(rows))


async def show_users(query, page: int = 0) -> None:
    data = await api.get("/admin/users", page=page, limit=PAGE_SIZE)
    items = data.get("items", [])
    rows = []
    lines = ["<b>👥 USER MANAGER</b>", "━━━━━━━━━━━━━━━━━━"]
    for u in items:
        uid = str(u.get("telegram_id"))
        name = short(u.get("username") or u.get("first_name") or uid, 24)
        lines.append(f"\n• <b>{esc(name)}</b> · <code>{esc(uid)}</code> · {esc(u.get('status', 'active'))}")
        rows.append([InlineKeyboardButton(f"👤 {name} ({uid})", callback_data=f"user:{uid}")])
    if not items:
        lines.append("No users found.")
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="nav:home")])
    await safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_user(query, telegram_id: str) -> None:
    data = await api.get(f"/admin/users/{telegram_id}")
    u = data.get("user", data)
    text = (
        "<b>👤 USER PROFILE</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"Name: <b>{esc(u.get('first_name'))}</b>\n"
        f"Username: @{esc(u.get('username'))}\n"
        f"Telegram ID: <code>{esc(telegram_id)}</code>\n"
        f"Status: <b>{esc(u.get('status', 'active'))}</b>\n"
        f"First Seen: <code>{fmt_time(u.get('first_seen'))}</code>\n"
        f"Last Active: <code>{fmt_time(u.get('last_seen'))}</code>\n"
        f"Total Keys: <b>{esc(u.get('total_keys', 0))}</b>\n"
        f"Active Keys: <b>{esc(u.get('active_keys', 0))}</b>\n"
        f"Devices: <b>{esc(u.get('device_count', 0))}</b>\n"
        f"Notes: {esc(u.get('notes'))}"
    )
    rows = [
        [InlineKeyboardButton("🚫 Ban User", callback_data=f"useract:ban:{telegram_id}"), InlineKeyboardButton("🟢 Unban", callback_data=f"useract:unban:{telegram_id}")],
        [InlineKeyboardButton("🔑 User Keys", callback_data=f"userkeys:{telegram_id}:0"), InlineKeyboardButton("📝 Edit Notes", callback_data=f"usernote:{telegram_id}")],
        [InlineKeyboardButton("⬅️ Users", callback_data="nav:users")],
    ]
    await safe_edit(query, text, InlineKeyboardMarkup(rows))


async def show_admins(query) -> None:
    data = await api.get("/admin/admins")
    admins = data.get("items", [])
    lines = ["<b>🛡 ADMIN MANAGER</b>", "━━━━━━━━━━━━━━━━━━"]
    rows = []
    for a in admins:
        uid = str(a.get("telegram_id"))
        lines.append(f"\n• <b>{esc(a.get('username') or a.get('name') or uid)}</b> · <code>{esc(uid)}</code> · {esc(a.get('role', 'admin'))}")
        rows.append([InlineKeyboardButton(f"⚙️ {uid}", callback_data=f"admin:{uid}")])
    if not admins:
        lines.append("No additional admins.")
    rows.append([InlineKeyboardButton("➕ Add Admin", callback_data="admin:add")])
    rows.append(back_menu())
    await safe_edit(query, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_bulk(query) -> None:
    text = (
        "<b>⚠️ OWNER BULK CONTROL</b>\n━━━━━━━━━━━━━━━━━━\n"
        "These actions affect multiple licenses. Each action requires a second confirmation.\n\n"
        "• Ban All: disables all currently active licenses.\n"
        "• Unban All: reactivates eligible banned licenses.\n"
        "• Delete All: permanently deletes license rows.\n"
        "• Ban + Delete: disables then deletes all licenses.\n\n"
        "A database backup should be taken before destructive actions."
    )
    rows = [
        [InlineKeyboardButton("🚫 Ban All Keys", callback_data="confirm:bulkban:all")],
        [InlineKeyboardButton("🟢 Unban All Keys", callback_data="confirm:bulkunban:all")],
        [InlineKeyboardButton("🗑 Delete All Keys", callback_data="confirm:bulkdelete:all")],
        [InlineKeyboardButton("☠️ Ban All + Delete All", callback_data="confirm:bulkban_delete:all")],
        back_menu(),
    ]
    await safe_edit(query, text, InlineKeyboardMarkup(rows))


async def confirm_action(query, action: str, target: str) -> None:
    labels = {
        "delete": "permanently delete this key", "templdelete": "delete this template",
        "bulkban": "BAN ALL KEYS", "bulkunban": "UNBAN ALL KEYS",
        "bulkdelete": "DELETE ALL KEYS PERMANENTLY", "bulkban_delete": "BAN AND DELETE ALL KEYS",
    }
    label = labels.get(action, action)
    text = f"<b>⚠️ CONFIRM ACTION</b>\n\nYou are about to <b>{esc(label)}</b>.\nTarget: <code>{esc(target)}</code>\n\nContinue?"
    rows = [[InlineKeyboardButton("✅ Confirm", callback_data=f"execute:{action}:{target}"), InlineKeyboardButton("❌ Cancel", callback_data="nav:home")]]
    await safe_edit(query, text, InlineKeyboardMarkup(rows))


async def execute_action(query, action: str, target: str) -> None:
    if action in {"bulkban", "bulkunban", "bulkdelete", "bulkban_delete", "adminremove"} and not is_owner(query.from_user.id):
        await query.answer("Owner only.", show_alert=True)
        return
    routes = {
        "delete": ("DELETE", f"/admin/keys/{target}"),
        "templdelete": ("DELETE", f"/admin/templates/{target}"),
        "adminremove": ("DELETE", f"/admin/admins/{target}"),
        "bulkban": ("POST", "/admin/keys/bulk/ban"),
        "bulkunban": ("POST", "/admin/keys/bulk/unban"),
        "bulkdelete": ("POST", "/admin/keys/bulk/delete"),
        "bulkban_delete": ("POST", "/admin/keys/bulk/ban-delete"),
    }
    method, path = routes[action]
    result = await api.request(method, path, json={"confirmation": "CONFIRMED", "target": target})
    count = result.get("affected", result.get("deleted", result.get("count", "Done")))
    await safe_edit(query, f"<b>✅ ACTION COMPLETED</b>\n{esc(action)}\nAffected: <b>{esc(count)}</b>", InlineKeyboardMarkup([back_menu()]))


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if q.data == "noop":
        return
    if not await require_admin(update, context):
        return
    data = q.data.split(":")
    action = data[0]
    try:
        if action == "nav":
            section = data[1]
            if section == "home":
                await show_home(update, context, edit=True)
            elif section == "generate":
                await show_generate(q)
            elif section == "stats":
                await show_stats(q)
            elif section == "users":
                await show_users(q)
            elif section == "admins":
                if not is_owner(q.from_user.id):
                    await q.answer("Owner only.", show_alert=True); return
                await show_admins(q)
            elif section == "bulk":
                if not is_owner(q.from_user.id):
                    await q.answer("Owner only.", show_alert=True); return
                await show_bulk(q)
            elif section == "health":
                health = await api.get("/health")
                await safe_edit(q, f"<b>🩺 API STATUS</b>\nWorker: <b>{esc(health.get('status', 'unknown'))}</b>\nDatabase: <b>{esc(health.get('database', 'unknown'))}</b>", InlineKeyboardMarkup([back_menu()]))
            elif section == "settings":
                await safe_edit(q, "<b>⚙️ SETTINGS</b>\nUse the controls below to manage default generation options.", InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Default Prefix", callback_data="setting:prefix"), InlineKeyboardButton("📱 Default Device Limit", callback_data="setting:devices")],
                    [InlineKeyboardButton("🧩 Default Product", callback_data="setting:product"), InlineKeyboardButton("🕒 Activation Mode", callback_data="setting:activation")], back_menu()
                ]))
            elif section == "search":
                context.user_data["state"] = "search"
                await safe_edit(q, "<b>🔎 SEARCH</b>\nSend a full Key Code, Telegram User ID, or username.", InlineKeyboardMarkup([back_menu()]))
            elif section == "bans":
                bans = await api.get("/admin/bans", page=0, limit=PAGE_SIZE)
                items = bans.get("items", [])
                lines = ["<b>🚫 BAN CENTER</b>", "━━━━━━━━━━━━━━━━━━"]
                for b in items:
                    lines.append(f"\n• <code>{esc(b.get('target'))}</code> — {esc(b.get('reason'))}")
                await safe_edit(q, "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("➕ Ban User", callback_data="banuser:new")], back_menu()]))
            elif section == "backup":
                if not is_owner(q.from_user.id):
                    await q.answer("Owner only.", show_alert=True); return
                result = await api.post("/admin/backup", {})
                await safe_edit(q, f"<b>💾 BACKUP REQUESTED</b>\n{esc(result.get('message', 'Backup job queued.'))}", InlineKeyboardMarkup([back_menu()]))
        elif action == "gen":
            choice = data[1]
            if choice == "custom":
                await prompt(update, context, "custom_duration", "Send duration in minutes (example: <code>90</code> for 1.5 hours). Send 0 for lifetime.")
                await safe_edit(q, "<b>✍️ CUSTOM DURATION</b>\nSend duration in minutes in your next message.")
            elif choice == "bulk":
                await prompt(update, context, "bulk_generate", "Send quantity and duration minutes separated by a space. Example: <code>25 1440</code>.")
                await safe_edit(q, "<b>📦 BULK GENERATION</b>\nSend: <code>quantity duration_minutes</code>\nExample: <code>25 1440</code>")
            else:
                await create_key(q, context, int(choice))
        elif action == "keys":
            await list_keys(q, int(data[1]), data[2])
        elif action == "key":
            await show_key(q, data[1])
        elif action == "act":
            op, key_id = data[1], data[2]
            if op == "extend":
                context.user_data["state"] = "extend_key"; context.user_data["target_id"] = key_id
                await safe_edit(q, "Send additional minutes to extend this key (example: 1440).")
            elif op == "devices":
                context.user_data["state"] = "device_limit"; context.user_data["target_id"] = key_id
                await safe_edit(q, "Send the new device limit (1–100).")
            else:
                route = {"ban": "ban", "unban": "unban", "disable": "disable", "activate": "activate", "unbind": "unbind", "reset": "reset"}[op]
                await api.post(f"/admin/keys/{key_id}/{route}", {})
                await show_key(q, key_id)
        elif action == "sharekey":
            key_id = data[1]
            result = await api.get(f"/admin/keys/{key_id}/share")
            message = result.get("message", "No rendered share message returned.")
            await safe_edit(q, f"<b>📤 SHARE MESSAGE</b>\n\n<pre>{esc(message)[:3500]}</pre>", InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Copy Message", callback_data=f"copyshare:{key_id}")],
                [InlineKeyboardButton("📝 Edit Templates", callback_data="templates:0")], back_menu()
            ]))
        elif action == "copyshare":
            result = await api.get(f"/admin/keys/{data[1]}/share")
            await q.answer("Share text is shown below; use Telegram copy/select.", show_alert=True)
            await q.message.reply_text(result.get("message", "No share message."))
        elif action == "templates":
            await show_templates(q, int(data[1]))
        elif action == "template":
            if data[1] == "new":
                context.user_data["state"] = "template_new_name"
                await safe_edit(q, "Send a name for the new share template.")
            else:
                await show_template(q, data[1])
        elif action == "templ_edit":
            field, tid = data[1], data[2]
            context.user_data["state"] = f"template_edit_{field}"; context.user_data["target_id"] = tid
            await safe_edit(q, "Send the new template name." if field == "name" else "Send the complete message template. Variables: {KEY}, {DURATION}, {EXPIRY}, {REMAINING}, {STATUS}, {USERNAME}, {USER_ID}, {DEVICE_LIMIT}, {CUSTOM_MESSAGE}.")
        elif action == "templ_act":
            op, tid = data[1], data[2]
            await api.post(f"/admin/templates/{tid}/{op}", {})
            await show_template(q, tid)
        elif action == "templ_preview":
            result = await api.get(f"/admin/templates/{data[1]}/preview")
            await safe_edit(q, f"<b>👁 TEMPLATE PREVIEW</b>\n\n<pre>{esc(result.get('message', ''))[:3500]}</pre>", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Template", callback_data=f"template:{data[1]}")]]))
        elif action == "user":
            await show_user(q, data[1])
        elif action == "useract":
            await api.post(f"/admin/users/{data[2]}/{data[1]}", {})
            await show_user(q, data[2])
        elif action == "userkeys":
            result = await api.get(f"/admin/users/{data[1]}/keys", page=int(data[2]), limit=PAGE_SIZE)
            lines = [f"<b>🔑 USER KEYS — {esc(data[1])}</b>"]
            rows = []
            for k in result.get("items", []):
                kid = str(k.get("id")); code = short(k.get("key_code"), 24)
                lines.append(f"\n• <code>{esc(code)}</code> — {esc(k.get('status'))}")
                rows.append([InlineKeyboardButton(code, callback_data=f"key:{kid}")])
            rows.append([InlineKeyboardButton("⬅️ User", callback_data=f"user:{data[1]}")])
            await safe_edit(q, "\n".join(lines), InlineKeyboardMarkup(rows))
        elif action == "usernote":
            context.user_data["state"] = "user_note"; context.user_data["target_id"] = data[1]
            await safe_edit(q, "Send the internal note for this user.")
        elif action == "admin":
            if data[1] == "add":
                context.user_data["state"] = "admin_add"
                await safe_edit(q, "Send the Telegram numeric ID of the new admin.")
            else:
                result = await api.get(f"/admin/admins/{data[1]}")
                a = result.get("admin", result)
                await safe_edit(q, f"<b>ADMIN</b>\nID: <code>{esc(data[1])}</code>\nRole: {esc(a.get('role'))}\nPermissions: {esc(', '.join(a.get('permissions', [])))}", InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑 Remove Admin", callback_data=f"confirm:adminremove:{data[1]}")],
                    [InlineKeyboardButton("⬅️ Admins", callback_data="nav:admins")]
                ]))
        elif action == "setting":
            key = data[1]
            context.user_data["state"] = f"setting_{key}"
            prompts = {"prefix": "Send the default key prefix (letters/numbers, max 12).", "devices": "Send default device limit (1–100).", "product": "Send default product identifier.", "activation": "Send <code>first_use</code> or <code>on_generation</code>."}
            await safe_edit(q, prompts[key])
        elif action == "logs":
            result = await api.get("/admin/logs", page=int(data[1]), limit=PAGE_SIZE)
            lines = ["<b>📜 ACTIVITY LOGS</b>"]
            for item in result.get("items", []):
                lines.append(f"\n<code>{fmt_time(item.get('created_at'))}</code>\n{esc(item.get('actor'))}: {esc(item.get('action'))} — {esc(item.get('target'))}")
            await safe_edit(q, "\n".join(lines), InlineKeyboardMarkup([back_menu()]))
        elif action == "history":
            result = await api.get(f"/admin/keys/{data[2]}/history")
            lines = ["<b>📜 KEY HISTORY</b>"]
            for item in result.get("items", []):
                lines.append(f"\n{fmt_time(item.get('created_at'))} · {esc(item.get('actor'))}\n{esc(item.get('action'))}")
            await safe_edit(q, "\n".join(lines), InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Key", callback_data=f"key:{data[2]}")]]))
        elif action == "confirm":
            await confirm_action(q, data[1], data[2])
        elif action == "execute":
            await execute_action(q, data[1], data[2])
        elif action == "banuser":
            context.user_data["state"] = "ban_user"
            await safe_edit(q, "Send Telegram ID and reason separated by a | character. Example: <code>123456789 | abuse</code>")
        else:
            await q.answer("Unknown action.", show_alert=True)
    except ApiError as exc:
        log.warning("API error in callback %s: %s", q.data, exc)
        await safe_edit(q, f"<b>⚠️ REQUEST FAILED</b>\n{esc(exc)}", InlineKeyboardMarkup([back_menu()]))
    except (ValueError, IndexError, KeyError) as exc:
        log.exception("Invalid callback payload: %s", q.data)
        await safe_edit(q, f"Invalid action data: {esc(exc)}", InlineKeyboardMarkup([back_menu()]))


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    state = context.user_data.get("state")
    if not state:
        await update.effective_message.reply_text("Use /start to open the dashboard.")
        return
    value = update.effective_message.text.strip()
    try:
        if state in {"custom_duration", "bulk_generate"}:
            if state == "custom_duration":
                minutes = int(value)
                if minutes < 0 or minutes > 5256000:
                    raise ValueError("Duration must be between 0 and 5,256,000 minutes.")
                context.user_data.pop("state", None)
                result = await api.post("/admin/keys/generate", {"duration_minutes": minutes, "quantity": 1, "telegram_id": update.effective_user.id, "prefix": context.user_data.get("key_prefix", "SUIII"), "device_limit": int(context.user_data.get("device_limit", 1)), "activation_mode": context.user_data.get("activation_mode", "first_use"), "product": context.user_data.get("product", "default")})
            else:
                qty, minutes = map(int, value.split())
                if not 1 <= qty <= 500 or not 0 <= minutes <= 5256000:
                    raise ValueError("Quantity must be 1–500 and duration 0–5,256,000 minutes.")
                context.user_data.pop("state", None)
                result = await api.post("/admin/keys/generate", {"duration_minutes": minutes, "quantity": qty, "telegram_id": update.effective_user.id, "prefix": context.user_data.get("key_prefix", "SUIII"), "device_limit": int(context.user_data.get("device_limit", 1)), "activation_mode": context.user_data.get("activation_mode", "first_use"), "product": context.user_data.get("product", "default")})
            keys = result.get("keys", [])
            codes = "\n".join(f"<code>{esc(k.get('key_code') if isinstance(k, dict) else k)}</code>" for k in keys[:50])
            await update.effective_message.reply_text(f"<b>✅ Generated {len(keys)} key(s)</b>\n{codes}", parse_mode=ParseMode.HTML, reply_markup=main_menu(is_owner(update.effective_user.id)))
        elif state == "extend_key":
            minutes = int(value)
            if minutes <= 0:
                raise ValueError("Enter a positive number of minutes.")
            kid = context.user_data["target_id"]
            await api.post(f"/admin/keys/{kid}/extend", {"minutes": minutes})
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Expiry extended.")
        elif state == "device_limit":
            limit = int(value)
            if not 1 <= limit <= 100:
                raise ValueError("Device limit must be between 1 and 100.")
            await api.patch(f"/admin/keys/{context.user_data['target_id']}", {"device_limit": limit})
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Device limit updated.")
        elif state == "template_new_name":
            if len(value) > 60:
                raise ValueError("Template name must be 60 characters or fewer.")
            result = await api.post("/admin/templates", {"name": value, "body": "SUIII_CHEATS PREMIUM\\n\\nKey: {KEY}\\nDuration: {DURATION}\\nExpiry: {EXPIRY}\\nRemaining: {REMAINING}\\n\\n{CUSTOM_MESSAGE}", "enabled": True})
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ Template created: {esc(result.get('template', {}).get('name', value))}", parse_mode=ParseMode.HTML)
        elif state in {"template_edit_name", "template_edit_body"}:
            field = "name" if state.endswith("name") else "body"
            if field == "name" and len(value) > 60:
                raise ValueError("Template name must be 60 characters or fewer.")
            if len(value) > 3500:
                raise ValueError("Template message must be 3500 characters or fewer.")
            await api.patch(f"/admin/templates/{context.user_data['target_id']}", {field: value})
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Template updated.")
        elif state == "admin_add":
            if not is_owner(update.effective_user.id):
                raise ApiError("Only the owner can add admins.")
            uid = int(value)
            await api.post("/admin/admins", {"telegram_id": uid, "role": "admin", "permissions": ["keys:read", "keys:generate"]})
            context.user_data.clear()
            await update.effective_message.reply_text(f"✅ Admin added: <code>{uid}</code>", parse_mode=ParseMode.HTML)
        elif state == "user_note":
            await api.patch(f"/admin/users/{context.user_data['target_id']}", {"notes": value[:1000]})
            context.user_data.clear()
            await update.effective_message.reply_text("✅ User note updated.")
        elif state == "search":
            context.user_data.clear()
            result = await api.get("/admin/search", q=value[:120])
            lines = ["<b>🔎 SEARCH RESULTS</b>"]
            rows = []
            for k in result.get("keys", [])[:10]:
                kid = str(k.get("id")); code = short(k.get("key_code"), 28)
                lines.append(f"\n🔑 <code>{esc(code)}</code> — {esc(k.get('status'))}")
                rows.append([InlineKeyboardButton(code, callback_data=f"key:{kid}")])
            for u in result.get("users", [])[:10]:
                uid = str(u.get("telegram_id")); name = short(u.get("username") or uid)
                lines.append(f"\n👤 {esc(name)} — <code>{esc(uid)}</code>")
                rows.append([InlineKeyboardButton(f"👤 {name}", callback_data=f"user:{uid}")])
            rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="nav:home")])
            await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        elif state == "ban_user":
            if not is_owner(update.effective_user.id):
                raise ApiError("Only owner can ban users from this panel.")
            parts = value.split("|", 1)
            uid = int(parts[0].strip())
            reason = parts[1].strip() if len(parts) > 1 else "Admin action"
            await api.post(f"/admin/users/{uid}/ban", {"reason": reason})
            context.user_data.clear()
            await update.effective_message.reply_text("✅ User banned.")
        elif state.startswith("setting_"):
            setting = state.removeprefix("setting_")
            if setting == "prefix" and not re.fullmatch(r"[A-Za-z0-9_-]{1,12}", value):
                raise ValueError("Prefix may contain only letters, numbers, _ or - (max 12).")
            if setting == "devices" and not 1 <= int(value) <= 100:
                raise ValueError("Device limit must be between 1 and 100.")
            if setting == "activation" and value not in {"first_use", "on_generation"}:
                raise ValueError("Use first_use or on_generation.")
            await api.patch("/admin/settings", {setting: int(value) if setting == "devices" else value})
            context.user_data.clear()
            await update.effective_message.reply_text("✅ Setting updated.")
        else:
            context.user_data.clear()
            await update.effective_message.reply_text("Unknown input state cleared. Use /start.")
    except (ValueError, ApiError) as exc:
        await update.effective_message.reply_text(f"⚠️ {esc(exc)}\nTry again, or send /cancel.", parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("Text action failed")
        await update.effective_message.reply_text("⚠️ Operation failed. Check Worker logs and try again.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled bot error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("An unexpected error occurred. Please try again.")


async def post_init(application: Application) -> None:
    log.info("SUIII_CHEATS bot started")
    try:
        health = await api.get("/health")
        log.info("Worker health: %s", health)
    except ApiError as exc:
        log.error("Worker health check failed: %s", exc)


async def post_shutdown(application: Application) -> None:
    await api.close()


def main() -> None:
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
