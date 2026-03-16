#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║       VPS SCRAPER — Telegram Bot  v2         ║
║       Fixed & Fast — Instant Results         ║
╚══════════════════════════════════════════════╝

Setup:
  pip install python-telegram-bot

Run:
  export TELEGRAM_BOT_TOKEN=your_token
  python vps_scraper_bot.py
"""

import os
import re
import asyncio
import logging
import urllib.request
from io import BytesIO
from datetime import datetime

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    )
except ImportError:
    print("❌  Run:  pip install python-telegram-bot")
    exit(1)

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# ══════════════════════════════════════════════════════════════════════════════
#  CURATED DATABASE
# ══════════════════════════════════════════════════════════════════════════════
FREE_VPS = [
    {"name": "Oracle Cloud",        "tag": "Oracle",     "specs": "2x AMD VM (1 OCPU 1GB) + 4x Arm VM (24GB total)", "price": "Always FREE",          "region": "Multiple",              "link": "https://www.oracle.com/cloud/free/",                                            "note": "Best free tier — Arm VMs are powerful ⭐"},
    {"name": "Google Cloud",        "tag": "GCP",        "specs": "e2-micro — 0.25 vCPU, 1GB RAM, 30GB HDD",        "price": "Always FREE",          "region": "US regions only",       "link": "https://cloud.google.com/free",                                                 "note": "Requires credit card"},
    {"name": "Amazon AWS",          "tag": "AWS",        "specs": "t2.micro — 1 vCPU, 1GB RAM",                     "price": "FREE 12 months",       "region": "Any",                   "link": "https://aws.amazon.com/free/",                                                  "note": "750 hrs/month included"},
    {"name": "Microsoft Azure",     "tag": "Azure",      "specs": "B1S — 1 vCPU, 1GB RAM",                          "price": "FREE 12mo + $200 credit","region": "Any",                  "link": "https://azure.microsoft.com/en-us/free/",                                       "note": "$200 credit for 30 days"},
    {"name": "Alibaba Cloud",       "tag": "Alibaba",    "specs": "ecs.t5 — 1 vCPU, 1GB RAM",                       "price": "FREE 3 months",        "region": "Asia / US / EU",        "link": "https://www.alibabacloud.com/campaign/free-trial",                              "note": "New users only"},
    {"name": "Huawei Cloud",        "tag": "Huawei",     "specs": "t6 micro — 1 vCPU, 1GB RAM, 40GB SSD",           "price": "FREE 1 year",          "region": "CN / EU / AP",          "link": "https://activity.huaweicloud.com/free_packages.html",                           "note": "New users only"},
    {"name": "Fly.io",              "tag": "Fly.io",     "specs": "shared-cpu-1x — 256MB RAM",                      "price": "Always FREE (3 VMs)",  "region": "Global",                "link": "https://fly.io/docs/about/pricing/",                                            "note": "Great for bots & apps"},
    {"name": "Render",              "tag": "Render",     "specs": "512MB RAM shared",                               "price": "Always FREE",          "region": "US / EU",               "link": "https://render.com/pricing",                                                    "note": "Sleeps after 15min idle"},
    {"name": "Kamatera",            "tag": "Kamatera",   "specs": "1 vCPU, 1GB RAM, 20GB SSD",                      "price": "FREE 30-day trial",    "region": "US / EU / Asia",        "link": "https://www.kamatera.com/express/compute/",                                     "note": "No credit card needed"},
    {"name": "IBM Cloud",           "tag": "IBM",        "specs": "256MB runtime",                                  "price": "Always FREE",          "region": "US / EU",               "link": "https://www.ibm.com/cloud/free",                                                "note": "Limited but no expiry"},
    {"name": "Cloudflare Tunnel",   "tag": "CF",         "specs": "Expose local server",                            "price": "Always FREE",          "region": "Global CDN",            "link": "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/","note": "Free public URL for local servers"},
    {"name": "Netcup Trial",        "tag": "Netcup",     "specs": "RS 1000 — 2 vCPU, 2GB RAM",                      "price": "FREE 30-day vouchers", "region": "Germany",               "link": "https://www.netcup.de/bestellen/gutschein.php",                                 "note": "Find promo codes on LowEndTalk"},
]

CHEAP_VPS = [
    {"name": "Hetzner Cloud",   "tag": "Hetzner",     "specs": "CAX11 — 2 Arm vCPU, 4GB RAM, 40GB NVMe",      "price": "€3.29/mo",          "region": "DE / FI / US",       "link": "https://www.hetzner.com/cloud",                          "note": "Best price/performance ⭐"},
    {"name": "Contabo VPS",     "tag": "Contabo",     "specs": "VPS S — 4 vCPU, 8GB RAM, 200GB SSD",          "price": "$6.99/mo",          "region": "US / EU / Asia",     "link": "https://contabo.com/en/vps/",                            "note": "Huge RAM for the price ⭐"},
    {"name": "Vultr Cloud",     "tag": "Vultr",       "specs": "1 vCPU, 1GB RAM, 25GB SSD",                   "price": "$6/mo",             "region": "32 locations",       "link": "https://www.vultr.com/pricing/",                         "note": "Hourly billing"},
    {"name": "DigitalOcean",    "tag": "DO",          "specs": "1 vCPU, 512MB RAM, 10GB SSD",                 "price": "$4/mo",             "region": "US / EU / Asia",     "link": "https://www.digitalocean.com/pricing/droplets",          "note": "$200 free credit (new users)"},
    {"name": "Linode",          "tag": "Linode",      "specs": "Nanode — 1 vCPU, 1GB RAM, 25GB SSD",          "price": "$5/mo",             "region": "Global",             "link": "https://www.linode.com/pricing/",                        "note": "$100 credit for new users"},
    {"name": "RackNerd",        "tag": "RackNerd",    "specs": "1 vCPU, 1.5GB RAM, 25GB SSD",                 "price": "~$10-15/yr",        "region": "US",                 "link": "https://racknerd.com",                                   "note": "Check LowEndBox for deals ⭐"},
    {"name": "Hostinger VPS",   "tag": "Hostinger",   "specs": "KVM 1 — 1 vCPU, 4GB RAM, 50GB SSD",          "price": "$3.99/mo (promo)",  "region": "US / EU / Asia",     "link": "https://www.hostinger.com/vps-hosting",                  "note": "AMD EPYC processors"},
    {"name": "BuyVM",           "tag": "BuyVM",       "specs": "Slice 512 — 1 vCPU, 512MB RAM, 10GB SSD",    "price": "$2/mo",             "region": "US / EU / LU",       "link": "https://buyvm.net/kvm-vps/",                             "note": "Often sold out"},
    {"name": "OVHcloud",        "tag": "OVH",         "specs": "Starter — 1 vCPU, 2GB RAM, 20GB SSD",        "price": "€3.59/mo",          "region": "EU / US / Asia",     "link": "https://www.ovhcloud.com/en/vps/",                       "note": "DDoS protection included"},
    {"name": "Scaleway",        "tag": "Scaleway",    "specs": "STARDUST1-S — 1 vCPU, 1GB RAM",              "price": "~€1.80/mo",         "region": "Paris / Amsterdam",  "link": "https://www.scaleway.com/en/pricing/",                   "note": "Very cheap EU option"},
    {"name": "IONOS VPS",       "tag": "IONOS",       "specs": "VPS S — 1 vCPU, 1GB RAM, 10GB SSD",          "price": "$1/mo (promo)",     "region": "US / EU",            "link": "https://www.ionos.com/servers/vps",                      "note": "Promo price for first months"},
    {"name": "LowEndBox Deals", "tag": "LEB",         "specs": "Various community deals",                     "price": "From $1/mo",        "region": "Global",             "link": "https://lowendbox.com",                                  "note": "Best aggregator for VPS deals ⭐"},
]

FREE_SSH = [
    {"name": "FastSSH",     "link": "https://www.fastssh.com/",    "note": "Daily free SSH/V2Ray/Xray accounts"},
    {"name": "SSHKit",      "link": "https://sshkit.com/",         "note": "Free SSH, 3-7 days validity"},
    {"name": "OpenTunnel",  "link": "https://opentunnel.net/",     "note": "Free SSH tunnel accounts"},
    {"name": "FreeSSH",     "link": "https://www.freessh.org/",    "note": "Free SSH accounts worldwide"},
    {"name": "SSHagan",     "link": "https://sshagan.net/",        "note": "Free SSH & VPN accounts"},
    {"name": "VPNJantit",   "link": "https://www.vpnjantit.com/",  "note": "Free SSH, OpenVPN, V2Ray"},
    {"name": "Goodssh",     "link": "https://goodssh.com/",        "note": "Free SSH accounts daily"},
    {"name": "SSH7Days",    "link": "https://ssh7days.com/",       "note": "7-day free SSH accounts"},
    {"name": "SSHstore",    "link": "https://sshstore.net/",       "note": "Premium & free SSH accounts"},
    {"name": "CPTS.me",     "link": "https://cpts.me/",            "note": "Free SSH 1-7 days"},
]

FREE_RDP = [
    {"name": "AWS Free Tier",        "link": "https://aws.amazon.com/free/",               "note": "t2.micro Windows — 750hr/mo free (12mo)"},
    {"name": "Azure Free RDP",       "link": "https://azure.microsoft.com/en-us/free/",    "note": "B1s Windows VM — 12mo free + $200 credit"},
    {"name": "GCP Windows VM",       "link": "https://cloud.google.com/free",              "note": "$300 credit — run Windows Server"},
    {"name": "Paperspace",           "link": "https://www.paperspace.com/",                "note": "Free tier cloud machines"},
    {"name": "Shadow PC Trial",      "link": "https://shadow.tech/",                       "note": "Cloud gaming PC free trial"},
    {"name": "Shells.com Trial",     "link": "https://www.shells.com/",                    "note": "Free trial cloud desktop"},
    {"name": "Now.gg",               "link": "https://now.gg/",                            "note": "Free cloud Android desktop"},
]

# ══════════════════════════════════════════════════════════════════════════════
#  LIVE SCRAPER
# ══════════════════════════════════════════════════════════════════════════════
def fetch(url: str, timeout: int = 8) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except:
        return ""


def scrape_lowendbox() -> list:
    deals = []
    html = fetch("https://lowendbox.com/")
    if not html:
        return deals
    titles = re.findall(r'class="entry-title"[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.DOTALL)
    prices = re.findall(r"\$[\d.]+(?:/(?:yr|mo|year|month))?", html, re.I)
    for i, t in enumerate(titles[:6]):
        clean = re.sub(r"<[^>]+>", "", t).strip()
        if clean:
            deals.append({
                "title": clean[:80],
                "price": prices[i] if i < len(prices) else "See site",
            })
    return deals


# ══════════════════════════════════════════════════════════════════════════════
#  FORMATTERS
# ══════════════════════════════════════════════════════════════════════════════
def fmt_free_vps() -> list[str]:
    pages, header = [], f"🆓 *FREE VPS — {len(FREE_VPS)} Sources*\n`──────────────────────────────`\n\n"
    body = ""
    for i, v in enumerate(FREE_VPS, 1):
        entry = (f"*{i}. {v['name']}* `[{v['tag']}]`\n"
                 f"   ⚙️ `{v['specs']}`\n"
                 f"   💵 {v['price']}  🌍 {v['region']}\n"
                 f"   💡 _{v['note']}_\n"
                 f"   🔗 {v['link']}\n\n")
        if len(header + body + entry) > 3800:
            pages.append(header + body.strip())
            header = "🆓 *FREE VPS (cont.)*\n\n"
            body = entry
        else:
            body += entry
    if body:
        pages.append(header + body.strip())
    return pages


def fmt_cheap_vps() -> list[str]:
    pages, header = [], f"💰 *CHEAP VPS — {len(CHEAP_VPS)} Providers*\n`──────────────────────────────`\n\n"
    body = ""
    for i, v in enumerate(CHEAP_VPS, 1):
        entry = (f"*{i}. {v['name']}* `[{v['tag']}]`\n"
                 f"   ⚙️ `{v['specs']}`\n"
                 f"   💵 {v['price']}  🌍 {v['region']}\n"
                 f"   💡 _{v['note']}_\n"
                 f"   🔗 {v['link']}\n\n")
        if len(header + body + entry) > 3800:
            pages.append(header + body.strip())
            header = "💰 *CHEAP VPS (cont.)*\n\n"
            body = entry
        else:
            body += entry
    if body:
        pages.append(header + body.strip())
    return pages


def fmt_ssh() -> list[str]:
    lines = [f"🔐 *FREE SSH SOURCES — {len(FREE_SSH)} Sites*\n`──────────────────────────────`\n"]
    for i, s in enumerate(FREE_SSH, 1):
        lines.append(f"*{i}. {s['name']}*\n   💡 _{s['note']}_\n   🔗 {s['link']}\n")
    lines.append("⚠️ _SSH accounts expire — create fresh ones daily from above sites._")
    return ["\n".join(lines)]


def fmt_rdp() -> list[str]:
    lines = [f"🖥 *FREE RDP / CLOUD DESKTOP — {len(FREE_RDP)} Sources*\n`──────────────────────────────`\n"]
    for i, r in enumerate(FREE_RDP, 1):
        lines.append(f"*{i}. {r['name']}*\n   💡 _{r['note']}_\n   🔗 {r['link']}\n")
    lines.append("⚠️ _Register on each site to get your free trial RDP._")
    return ["\n".join(lines)]


def fmt_live(deals: list) -> str:
    if not deals:
        return "⚠️ Could not fetch live deals right now. Try again in a moment.\n\n💡 Visit https://lowendbox.com manually for the latest deals."
    lines = ["📡 *LIVE VPS DEALS from LowEndBox*\n`──────────────────────────────`\n"]
    for d in deals:
        lines.append(f"🔥 {d['title']}\n   💵 {d['price']} — https://lowendbox.com\n")
    lines.append("🔗 More deals: https://lowendbox.com | https://lowendtalk.com")
    return "\n".join(lines)


def to_txt() -> str:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    out = [f"VPS SCRAPER RESULTS — {now}", "=" * 52, ""]
    out += ["=== FREE VPS ==="]
    for v in FREE_VPS:
        out += [f"[{v['tag']}] {v['name']}", f"  Specs : {v['specs']}", f"  Price : {v['price']}",
                f"  Region: {v['region']}", f"  Link  : {v['link']}", f"  Note  : {v['note']}", ""]
    out += ["=== CHEAP VPS ==="]
    for v in CHEAP_VPS:
        out += [f"[{v['tag']}] {v['name']}", f"  Specs : {v['specs']}", f"  Price : {v['price']}",
                f"  Region: {v['region']}", f"  Link  : {v['link']}", f"  Note  : {v['note']}", ""]
    out += ["=== FREE SSH ==="]
    for s in FREE_SSH:
        out += [f"{s['name']}", f"  {s['note']}", f"  {s['link']}", ""]
    out += ["=== FREE RDP ==="]
    for r in FREE_RDP:
        out += [f"{r['name']}", f"  {r['note']}", f"  {r['link']}", ""]
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARD
# ══════════════════════════════════════════════════════════════════════════════
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆓 Free VPS",   callback_data="free"),
         InlineKeyboardButton("💰 Cheap VPS",  callback_data="cheap")],
        [InlineKeyboardButton("🔐 Free SSH",   callback_data="ssh"),
         InlineKeyboardButton("🖥 Free RDP",   callback_data="rdp")],
        [InlineKeyboardButton("📡 Live Deals", callback_data="live"),
         InlineKeyboardButton("📥 Export All", callback_data="export")],
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  SEND HELPER
# ══════════════════════════════════════════════════════════════════════════════
async def send_pages(fn, pages: list[str]):
    for page in pages:
        try:
            await fn(page, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception:
            await fn(page, disable_web_page_preview=True)
        await asyncio.sleep(0.4)


# ══════════════════════════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🖥 *VPS Scraper Bot*\n\n"
        "Instant free & cheap VPS, SSH, and RDP listings.\n\n"
        "/free   — Free VPS (Oracle, AWS, GCP, Azure...)\n"
        "/cheap  — Cheap VPS (Hetzner, Vultr, Contabo...)\n"
        "/ssh    — Free SSH account sites\n"
        "/rdp    — Free RDP / cloud desktop\n"
        "/live   — Live deals from LowEndBox\n"
        "/all    — Show everything\n"
        "/export — Download full list as .txt"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())


async def cmd_free(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_pages(update.message.reply_text, fmt_free_vps())
    await update.message.reply_text("Tap a button for more:", reply_markup=main_kb())


async def cmd_cheap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_pages(update.message.reply_text, fmt_cheap_vps())
    await update.message.reply_text("Tap a button for more:", reply_markup=main_kb())


async def cmd_ssh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_pages(update.message.reply_text, fmt_ssh())


async def cmd_rdp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_pages(update.message.reply_text, fmt_rdp())


async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📡 Fetching live deals from LowEndBox...")
    loop = asyncio.get_event_loop()
    deals = await loop.run_in_executor(None, scrape_lowendbox)
    result = fmt_live(deals)
    try:
        await msg.edit_text(result, parse_mode="Markdown", disable_web_page_preview=True)
    except:
        await msg.edit_text(result, disable_web_page_preview=True)


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📦 Sending all results...")
    for pages in [fmt_free_vps(), fmt_cheap_vps(), fmt_ssh(), fmt_rdp()]:
        await send_pages(update.message.reply_text, pages)
    await update.message.reply_text("✅ All done!", reply_markup=main_kb())


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    content = to_txt()
    bio = BytesIO(content.encode())
    bio.name = f"vps_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt"
    await update.message.reply_document(
        document=bio,
        caption=f"📥 *VPS Full List*\n{len(FREE_VPS)} free + {len(CHEAP_VPS)} cheap + {len(FREE_SSH)} SSH + {len(FREE_RDP)} RDP",
        parse_mode="Markdown",
    )


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    send = q.message.reply_text

    if q.data == "free":
        await send_pages(send, fmt_free_vps())
    elif q.data == "cheap":
        await send_pages(send, fmt_cheap_vps())
    elif q.data == "ssh":
        await send_pages(send, fmt_ssh())
    elif q.data == "rdp":
        await send_pages(send, fmt_rdp())
    elif q.data == "live":
        msg = await send("📡 Fetching live deals...")
        loop = asyncio.get_event_loop()
        deals = await loop.run_in_executor(None, scrape_lowendbox)
        result = fmt_live(deals)
        try:
            await msg.edit_text(result, parse_mode="Markdown", disable_web_page_preview=True)
        except:
            await msg.edit_text(result, disable_web_page_preview=True)
    elif q.data == "export":
        content = to_txt()
        bio = BytesIO(content.encode())
        bio.name = f"vps_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt"
        await q.message.reply_document(
            document=bio,
            caption="📥 *VPS Full List Export*",
            parse_mode="Markdown",
        )


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌  Set your token:")
        print("    export TELEGRAM_BOT_TOKEN=your_token_here")
        print("    OR edit BOT_TOKEN at the top of this file.")
        exit(1)

    print("🖥  VPS Scraper Bot v2 running... Ctrl+C to stop.\n")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_start))
    app.add_handler(CommandHandler("free",   cmd_free))
    app.add_handler(CommandHandler("cheap",  cmd_cheap))
    app.add_handler(CommandHandler("ssh",    cmd_ssh))
    app.add_handler(CommandHandler("rdp",    cmd_rdp))
    app.add_handler(CommandHandler("live",   cmd_live))
    app.add_handler(CommandHandler("all",    cmd_all))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CallbackQueryHandler(on_callback))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
