# PUBG Auto-Feedback Server

Chhota Flask receiver jo PUBG auto-feedback Lua script se aane wala
base64 screenshot + caption Telegram pe bhejta hai.

## Endpoints
- `GET /` — health check
- `POST /feedback` — `base64_image` + `caption` form fields

## Env vars
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

## Run (local)
