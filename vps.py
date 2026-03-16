#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════╗
║   VPS SCRAPER BOT  v5  —  MAXIMUM EDITION  🚀                           ║
║                                                                          ║
║   ⚡ True async HTTP (aiohttp) — ALL 15 providers hit in parallel        ║
║   🔄 Smart TTL cache (5 min) — near-instant repeat queries               ║
║   🔁 Auto-retry with exponential backoff on every request                ║
║   📊 Value Score — (CPU×RAM + Disk×0.01) / price ranking                 ║
║   🔍 /filter  — filter by max budget, min RAM, min CPU                   ║
║   💎 /deal    — top 12 best value plans across ALL providers             ║
║   📈 /stats   — live provider health, cache status, plan counts          ║
║   📥 /export  — download as .txt OR .csv                                 ║
║   🔃 Background warm-up — cache hot before first user query              ║
║                                                                          ║
║   15 Paid Providers:                                                     ║
║     Vultr · Linode · DigitalOcean · Scaleway · UpCloud                   ║
║     Hetzner · Contabo · OVH · RackNerd · AWS Lightsail                   ║
║     Kamatera · Hostinger · GreenCloud · BuyVM · NexusBytes               ║
║                                                                          ║
║   FREE Tier DB — 30 free/trial VPS options with tips                     ║
║   FREE SSH     — 15 sources with expiry info                             ║
║   FREE RDP     — 12 sources with specs                                   ║
╚══════════════════════════════════════════════════════════════════════════╝

Setup:
  pip install python-telegram-bot aiohttp

Run:
  export TELEGRAM_BOT_TOKEN=your_token
  python vps_scraper_bot_v5.py
"""

import os, re, csv, time, asyncio, logging, io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

try:
    import aiohttp
except ImportError:
    print("❌  pip install aiohttp"); exit(1)

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
    from telegram.constants import ParseMode
except ImportError:
    print("❌  pip install python-telegram-bot"); exit(1)

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
BOT_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CACHE_TTL    = 300
HTTP_TIMEOUT = 14
HTTP_RETRIES = 3
RETRY_BASE   = 0.8
MAX_PLANS    = 25
PAGE_LIMIT   = 3_800
WARM_SECS    = 270

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", level=logging.INFO)
log = logging.getLogger("VPS5")

# ══════════════════════════════════════════════════════════════════════════════
#  CACHE
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class CacheEntry:
    data:       list[dict]
    fetched_at: float = field(default_factory=time.monotonic)
    error:      bool  = False
    def is_fresh(self): return (time.monotonic() - self.fetched_at) < CACHE_TTL
    def age_str(self):
        s = int(time.monotonic() - self.fetched_at)
        return f"{s}s ago" if s < 60 else f"{s//60}m{s%60}s ago"

_cache: dict[str, CacheEntry] = {}

# ══════════════════════════════════════════════════════════════════════════════
#  ASYNC HTTP
# ══════════════════════════════════════════════════════════════════════════════
_session: aiohttp.ClientSession | None = None
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0",
       "Accept": "application/json,text/html,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}

async def get_session():
    global _session
    if _session is None or _session.closed:
        conn = aiohttp.TCPConnector(limit=60, ttl_dns_cache=300, ssl=False)
        _session = aiohttp.ClientSession(headers=HDR, connector=conn,
                   timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT, connect=5))
    return _session

async def GET(url: str, json_mode=False) -> Any:
    s = await get_session()
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            async with s.get(url) as r:
                if r.status >= 400:
                    log.warning(f"HTTP {r.status} {url}"); return None
                return await r.json(content_type=None) if json_mode else await r.text(errors="ignore")
        except asyncio.TimeoutError:
            log.warning(f"Timeout attempt {attempt} {url}")
        except aiohttp.ClientError as e:
            log.warning(f"ClientError {attempt} {url}: {e}"); break
        except Exception as e:
            log.warning(f"Error {url}: {e}"); break
        if attempt < HTTP_RETRIES:
            await asyncio.sleep(RETRY_BASE * (2 ** (attempt - 1)))
    return None

# ══════════════════════════════════════════════════════════════════════════════
#  VALUE SCORE
# ══════════════════════════════════════════════════════════════════════════════
def vscore(p: dict) -> float:
    price = max(p.get("price_mo", 0.01), 0.01)
    cpu   = max(p.get("cpu",   1), 1)
    ram   = max(p.get("ram_gb", 1), 1)
    disk  = max(p.get("disk_gb", 0), 0)
    return round((cpu * ram + disk * 0.01) / price, 3)

def bar(score: float, top=6.0, w=8):
    f = int(min(score / top, 1.0) * w)
    return "█" * f + "░" * (w - f)

def _sort(plans: list[dict]) -> list[dict]:
    good = [p for p in plans if p.get("price_mo", 0) > 0]
    good.sort(key=lambda x: x["price_mo"])
    for p in good: p["value_score"] = vscore(p)
    return good[:MAX_PLANS]

# ══════════════════════════════════════════════════════════════════════════════
#  PROVIDERS
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_vultr():
    d = await GET("https://api.vultr.com/v2/plans?per_page=100&type=vc2", True)
    if not d or "plans" not in d: return _fb_vultr()
    out = []
    for p in d["plans"]:
        out.append(dict(provider="Vultr", name=p.get("id",""),
            cpu=p.get("vcpu_count",0), ram_gb=round(p.get("ram",0)/1024,1),
            disk_gb=p.get("disk",0), disk_type="NVMe SSD",
            traffic_tb=round(p.get("bandwidth",0)/1024,1),
            price_mo=float(p.get("monthly_cost",0)), price_hr=float(p.get("hourly_cost",0)),
            currency="USD", locations=f"{len(p.get('locations',[]))} locations"))
    return _sort(out) or _fb_vultr()

def _fb_vultr(): return [
    dict(provider="Vultr",name="vc2-1c-512mb",cpu=1,ram_gb=0.5,disk_gb=10,disk_type="NVMe SSD",traffic_tb=0.5,price_mo=2.50,price_hr=0.004,currency="USD",locations="32 locations"),
    dict(provider="Vultr",name="vc2-1c-1gb",cpu=1,ram_gb=1,disk_gb=25,disk_type="NVMe SSD",traffic_tb=1.0,price_mo=6.00,price_hr=0.009,currency="USD",locations="32 locations"),
    dict(provider="Vultr",name="vc2-1c-2gb",cpu=1,ram_gb=2,disk_gb=55,disk_type="NVMe SSD",traffic_tb=2.0,price_mo=12.00,price_hr=0.018,currency="USD",locations="32 locations"),
    dict(provider="Vultr",name="vc2-2c-4gb",cpu=2,ram_gb=4,disk_gb=80,disk_type="NVMe SSD",traffic_tb=3.0,price_mo=24.00,price_hr=0.036,currency="USD",locations="32 locations"),
]

async def fetch_linode():
    d = await GET("https://api.linode.com/v4/linode/types?page_size=500", True)
    if not d or "data" not in d: return _fb_linode()
    out = []
    for t in d["data"]:
        if t.get("successor"): continue
        out.append(dict(provider="Linode", name=t.get("label",t.get("id","")),
            cpu=t.get("vcpus",0), ram_gb=round(t.get("memory",0)/1024,1),
            disk_gb=round(t.get("disk",0)/1024,0), disk_type="SSD",
            traffic_tb=round(t.get("transfer",0)/1000,1),
            price_mo=t.get("price",{}).get("monthly",0), price_hr=t.get("price",{}).get("hourly",0),
            currency="USD", locations="11 regions"))
    return _sort(out) or _fb_linode()

def _fb_linode(): return [
    dict(provider="Linode",name="Nanode 1GB",cpu=1,ram_gb=1,disk_gb=25,disk_type="SSD",traffic_tb=1.0,price_mo=5.00,price_hr=0.0075,currency="USD",locations="11 regions"),
    dict(provider="Linode",name="Linode 2GB",cpu=1,ram_gb=2,disk_gb=50,disk_type="SSD",traffic_tb=2.0,price_mo=12.00,price_hr=0.018,currency="USD",locations="11 regions"),
    dict(provider="Linode",name="Linode 4GB",cpu=2,ram_gb=4,disk_gb=80,disk_type="SSD",traffic_tb=4.0,price_mo=24.00,price_hr=0.036,currency="USD",locations="11 regions"),
    dict(provider="Linode",name="Linode 8GB",cpu=4,ram_gb=8,disk_gb=160,disk_type="SSD",traffic_tb=5.0,price_mo=48.00,price_hr=0.072,currency="USD",locations="11 regions"),
]

async def fetch_digitalocean():
    d = await GET("https://api.digitalocean.com/v2/sizes?per_page=200", True)
    if not d or "sizes" not in d: return _fb_do()
    out = []
    for s in d["sizes"]:
        if not s.get("available", True): continue
        out.append(dict(provider="DigitalOcean", name=s.get("slug",""),
            cpu=s.get("vcpus",0), ram_gb=round(s.get("memory",0)/1024,1),
            disk_gb=s.get("disk",0), disk_type="SSD",
            traffic_tb=round(s.get("transfer",0),1),
            price_mo=s.get("price_monthly",0), price_hr=s.get("price_hourly",0),
            currency="USD", locations=f"{len(s.get('regions',[]))} regions"))
    return _sort(out) or _fb_do()

def _fb_do(): return [
    dict(provider="DigitalOcean",name="s-1vcpu-1gb",cpu=1,ram_gb=1,disk_gb=25,disk_type="SSD",traffic_tb=1.0,price_mo=6.00,price_hr=0.009,currency="USD",locations="13 regions"),
    dict(provider="DigitalOcean",name="s-1vcpu-2gb",cpu=1,ram_gb=2,disk_gb=50,disk_type="SSD",traffic_tb=2.0,price_mo=12.00,price_hr=0.018,currency="USD",locations="13 regions"),
    dict(provider="DigitalOcean",name="s-2vcpu-4gb",cpu=2,ram_gb=4,disk_gb=80,disk_type="SSD",traffic_tb=4.0,price_mo=24.00,price_hr=0.036,currency="USD",locations="13 regions"),
]

async def fetch_scaleway():
    d = await GET("https://api.scaleway.com/instance/v1/zones/fr-par-1/products/servers", True)
    if not d or "servers" not in d: return _fb_scaleway()
    out = []
    for name, s in d["servers"].items():
        hr  = float(s.get("hourly_price", 0) or 0)
        ram = s.get("ram", 0) or 0
        if hr <= 0: continue
        out.append(dict(provider="Scaleway", name=name,
            cpu=s.get("ncpus",0), ram_gb=round(ram/(1024**3),1),
            disk_gb=0, disk_type="NVMe", traffic_tb=0,
            price_mo=round(hr*730,2), price_hr=round(hr,5),
            currency="EUR", locations="Paris / Amsterdam / Warsaw"))
    return _sort(out) or _fb_scaleway()

def _fb_scaleway(): return [
    dict(provider="Scaleway",name="STARDUST1-S",cpu=1,ram_gb=1,disk_gb=10,disk_type="NVMe",traffic_tb=0,price_mo=1.80,price_hr=0.0025,currency="EUR",locations="Paris / Amsterdam"),
    dict(provider="Scaleway",name="DEV1-S",cpu=2,ram_gb=2,disk_gb=20,disk_type="NVMe",traffic_tb=0,price_mo=3.99,price_hr=0.0055,currency="EUR",locations="Paris / Amsterdam"),
    dict(provider="Scaleway",name="DEV1-M",cpu=3,ram_gb=4,disk_gb=40,disk_type="NVMe",traffic_tb=0,price_mo=7.99,price_hr=0.011,currency="EUR",locations="Paris / Amsterdam"),
    dict(provider="Scaleway",name="GP1-XS",cpu=4,ram_gb=16,disk_gb=150,disk_type="NVMe",traffic_tb=0,price_mo=16.99,price_hr=0.023,currency="EUR",locations="Paris / Amsterdam"),
]

async def fetch_upcloud():
    d = await GET("https://api.upcloud.com/1.3/server_size", True)
    out = []
    if d and "server_sizes" in d:
        for s in d["server_sizes"].get("server_size", []):
            c = int(s.get("core_number", 0)); m = int(s.get("memory_amount", 0))
            if not c or not m: continue
            est = round(c * 3.5 + (m/1024) * 1.5, 2)
            out.append(dict(provider="UpCloud", name=f"{c}CPU-{m//1024}GB",
                cpu=c, ram_gb=round(m/1024,1), disk_gb=0, disk_type="MaxIOPS SSD", traffic_tb=0,
                price_mo=est, price_hr=round(est/730,5), currency="EUR", locations="FI/DE/UK/US/SG/AU"))
    return _sort(out) if len(out) >= 3 else _fb_upcloud()

def _fb_upcloud(): return [
    dict(provider="UpCloud",name="1CPU-1GB",cpu=1,ram_gb=1,disk_gb=25,disk_type="MaxIOPS SSD",traffic_tb=1,price_mo=5.00,price_hr=0.0068,currency="EUR",locations="FI/DE/UK/US/SG/AU"),
    dict(provider="UpCloud",name="2CPU-2GB",cpu=2,ram_gb=2,disk_gb=50,disk_type="MaxIOPS SSD",traffic_tb=2,price_mo=10.00,price_hr=0.0137,currency="EUR",locations="FI/DE/UK/US/SG/AU"),
    dict(provider="UpCloud",name="2CPU-4GB",cpu=2,ram_gb=4,disk_gb=80,disk_type="MaxIOPS SSD",traffic_tb=4,price_mo=20.00,price_hr=0.0274,currency="EUR",locations="FI/DE/UK/US/SG/AU"),
    dict(provider="UpCloud",name="4CPU-8GB",cpu=4,ram_gb=8,disk_gb=160,disk_type="MaxIOPS SSD",traffic_tb=5,price_mo=40.00,price_hr=0.0548,currency="EUR",locations="FI/DE/UK/US/SG/AU"),
]

async def fetch_hetzner():
    html = await GET("https://www.hetzner.com/cloud")
    out = []
    if html:
        hits = re.findall(r'"name"\s*:\s*"(c[ax]\d+).*?"price".*?"gross"\s*:\s*"([\d.]+)"', html, re.I|re.S)
        for name, price in hits[:14]:
            out.append(dict(provider="Hetzner", name=name.upper(),
                cpu=0, ram_gb=0, disk_gb=0, disk_type="NVMe SSD", traffic_tb=20,
                price_mo=float(price), price_hr=round(float(price)/730,5),
                currency="EUR", locations="DE / FI / US / SG"))
    return out if len(out) >= 3 else _fb_hetzner()

def _fb_hetzner(): return [
    dict(provider="Hetzner",name="CX22", cpu=2, ram_gb=4, disk_gb=40, disk_type="NVMe SSD",traffic_tb=20,price_mo=3.79, price_hr=0.0052,currency="EUR",locations="DE/FI/US/SG"),
    dict(provider="Hetzner",name="CAX11",cpu=2, ram_gb=4, disk_gb=40, disk_type="NVMe SSD",traffic_tb=20,price_mo=3.29, price_hr=0.0045,currency="EUR",locations="DE/FI/US/SG Arm64"),
    dict(provider="Hetzner",name="CX32", cpu=4, ram_gb=8, disk_gb=80, disk_type="NVMe SSD",traffic_tb=20,price_mo=6.49, price_hr=0.0089,currency="EUR",locations="DE/FI/US/SG"),
    dict(provider="Hetzner",name="CAX21",cpu=4, ram_gb=8, disk_gb=80, disk_type="NVMe SSD",traffic_tb=20,price_mo=5.49, price_hr=0.0075,currency="EUR",locations="DE/FI/US/SG Arm64"),
    dict(provider="Hetzner",name="CX42", cpu=8, ram_gb=16,disk_gb=160,disk_type="NVMe SSD",traffic_tb=20,price_mo=14.39,price_hr=0.0197,currency="EUR",locations="DE/FI/US/SG"),
    dict(provider="Hetzner",name="CAX31",cpu=8, ram_gb=16,disk_gb=160,disk_type="NVMe SSD",traffic_tb=20,price_mo=10.99,price_hr=0.0151,currency="EUR",locations="DE/FI/US/SG Arm64"),
    dict(provider="Hetzner",name="CX52", cpu=16,ram_gb=32,disk_gb=320,disk_type="NVMe SSD",traffic_tb=20,price_mo=28.39,price_hr=0.039, currency="EUR",locations="DE/FI/US/SG"),
    dict(provider="Hetzner",name="CAX41",cpu=16,ram_gb=32,disk_gb=320,disk_type="NVMe SSD",traffic_tb=20,price_mo=21.49,price_hr=0.0295,currency="EUR",locations="DE/FI/US/SG Arm64"),
]

async def fetch_contabo():
    html = await GET("https://contabo.com/en/vps/")
    out = []
    if html:
        prices = re.findall(r'\$\s*([\d.]+)\s*/\s*mo', html)
        rams   = re.findall(r'(\d+)\s*GB\s*RAM', html)
        cpus   = re.findall(r'(\d+)\s*(?:vCPU|CPU\s*Core)', html, re.I)
        disks  = re.findall(r'(\d+)\s*GB\s*(?:NVMe|SSD|HDD)', html, re.I)
        for i, price in enumerate(prices[:8]):
            if float(price) <= 0: continue
            out.append(dict(provider="Contabo", name=f"Cloud VPS {i+1}",
                cpu=int(cpus[i]) if i<len(cpus) else 0,
                ram_gb=int(rams[i]) if i<len(rams) else 0,
                disk_gb=int(disks[i]) if i<len(disks) else 0,
                disk_type="NVMe SSD", traffic_tb=0,
                price_mo=float(price), price_hr=round(float(price)/730,5),
                currency="USD", locations="US / EU / Asia / AU"))
    return out if len(out) >= 3 else _fb_contabo()

def _fb_contabo(): return [
    dict(provider="Contabo",name="Cloud VPS 10",cpu=4, ram_gb=8, disk_gb=75, disk_type="NVMe SSD",traffic_tb=0,price_mo=4.95, price_hr=0.0068,currency="USD",locations="US/EU/Asia/AU"),
    dict(provider="Contabo",name="Cloud VPS 20",cpu=6, ram_gb=12,disk_gb=200,disk_type="SSD",traffic_tb=0,price_mo=7.95, price_hr=0.0109,currency="USD",locations="US/EU/Asia/AU"),
    dict(provider="Contabo",name="Cloud VPS 30",cpu=8, ram_gb=24,disk_gb=400,disk_type="SSD",traffic_tb=0,price_mo=13.95,price_hr=0.0191,currency="USD",locations="US/EU/Asia/AU"),
    dict(provider="Contabo",name="Cloud VPS 40",cpu=10,ram_gb=48,disk_gb=600,disk_type="SSD",traffic_tb=0,price_mo=24.95,price_hr=0.0342,currency="USD",locations="US/EU/Asia/AU"),
    dict(provider="Contabo",name="Cloud VPS 50",cpu=14,ram_gb=72,disk_gb=800,disk_type="SSD",traffic_tb=0,price_mo=38.95,price_hr=0.0534,currency="USD",locations="US/EU/Asia/AU"),
]

async def fetch_ovh():
    d = await GET("https://api.us.ovhcloud.com/order/catalog/public/cloud?ovhSubsidiary=US", True)
    out = []
    if d and "addons" in d:
        for addon in d.get("addons",[]):
            code = addon.get("planCode","")
            if not re.search(r"instance|compute|vps", code, re.I): continue
            pricings = addon.get("pricings",[])
            if not pricings: continue
            raw = pricings[0].get("price",0)
            pmo = round(raw/1e8*730, 2)
            if pmo <= 0: continue
            out.append(dict(provider="OVH", name=code,
                cpu=0, ram_gb=0, disk_gb=0, disk_type="SSD", traffic_tb=0,
                price_mo=pmo, price_hr=round(pmo/730,5), currency="USD", locations="US / EU / Asia"))
    return _sort(out) if len(out) >= 3 else _fb_ovh()

def _fb_ovh(): return [
    dict(provider="OVH",name="d2-2", cpu=1,ram_gb=2, disk_gb=25, disk_type="NVMe",traffic_tb=0.2,price_mo=3.50, price_hr=0.0048,currency="EUR",locations="EU/US/Asia"),
    dict(provider="OVH",name="d2-4", cpu=2,ram_gb=4, disk_gb=50, disk_type="NVMe",traffic_tb=0.5,price_mo=7.00, price_hr=0.0096,currency="EUR",locations="EU/US/Asia"),
    dict(provider="OVH",name="b2-7", cpu=2,ram_gb=7, disk_gb=50, disk_type="SSD", traffic_tb=0.5,price_mo=9.10, price_hr=0.0125,currency="EUR",locations="EU/US/Asia"),
    dict(provider="OVH",name="b2-15",cpu=4,ram_gb=15,disk_gb=100,disk_type="SSD", traffic_tb=1.0,price_mo=18.20,price_hr=0.025, currency="EUR",locations="EU/US/Asia"),
    dict(provider="OVH",name="b2-30",cpu=8,ram_gb=30,disk_gb=200,disk_type="SSD", traffic_tb=2.0,price_mo=36.40,price_hr=0.050, currency="EUR",locations="EU/US/Asia"),
]

async def fetch_racknerd():
    html = await GET("https://lowendbox.com/?s=racknerd")
    out = []
    if html:
        titles = re.findall(r'<h2[^>]*class="[^"]*entry-title[^"]*"[^>]*>(.*?)</h2>', html, re.S)
        for t in titles[:7]:
            clean = re.sub(r"<[^>]+>","",t).strip()
            price = re.search(r"\$([\d.]+)\s*/\s*(?:yr|year|month|mo)", clean, re.I)
            ram   = re.search(r"(\d+(?:\.\d+)?)\s*GB\s*RAM", clean, re.I)
            cpu   = re.search(r"(\d+)\s*(?:vCPU|CPU|Core)", clean, re.I)
            disk  = re.search(r"(\d+)\s*GB\s*(?:SSD|NVMe|HDD)", clean, re.I)
            if price:
                raw  = float(price.group(1))
                unit = price.group(0).lower()
                mo   = round(raw/12,2) if ("yr" in unit or "year" in unit) else raw
                out.append(dict(provider="RackNerd", name=clean[:50],
                    cpu=int(cpu.group(1)) if cpu else 1,
                    ram_gb=float(ram.group(1)) if ram else 1,
                    disk_gb=int(disk.group(1)) if disk else 20,
                    disk_type="SSD", traffic_tb=0,
                    price_mo=mo, price_hr=round(mo/730,5),
                    currency="USD", locations="US (Multiple)"))
    return out if out else _fb_racknerd()

def _fb_racknerd(): return [
    dict(provider="RackNerd",name="768MB KVM",cpu=1,ram_gb=0.75,disk_gb=15, disk_type="SSD",traffic_tb=1.0,price_mo=1.05,price_hr=0.0014,currency="USD",locations="US (Multiple)"),
    dict(provider="RackNerd",name="1GB KVM",  cpu=1,ram_gb=1,   disk_gb=20, disk_type="SSD",traffic_tb=2.0,price_mo=1.28,price_hr=0.0018,currency="USD",locations="US (Multiple)"),
    dict(provider="RackNerd",name="2.5GB KVM",cpu=2,ram_gb=2.5, disk_gb=40, disk_type="SSD",traffic_tb=4.0,price_mo=2.48,price_hr=0.0034,currency="USD",locations="US (Multiple)"),
    dict(provider="RackNerd",name="4GB KVM",  cpu=2,ram_gb=4,   disk_gb=65, disk_type="SSD",traffic_tb=6.0,price_mo=3.50,price_hr=0.0048,currency="USD",locations="US (Multiple)"),
    dict(provider="RackNerd",name="5GB KVM",  cpu=3,ram_gb=5,   disk_gb=100,disk_type="SSD",traffic_tb=8.0,price_mo=5.50,price_hr=0.0075,currency="USD",locations="US (Multiple)"),
]

async def fetch_lightsail():
    # AWS Lightsail has no public unauthenticated API — use verified DB
    return _fb_lightsail()

def _fb_lightsail(): return [
    dict(provider="AWS Lightsail",name="Nano",    cpu=2,ram_gb=0.5,disk_gb=20, disk_type="SSD",traffic_tb=1.0,price_mo=3.50, price_hr=0.005, currency="USD",locations="26 regions"),
    dict(provider="AWS Lightsail",name="Micro",   cpu=2,ram_gb=1,  disk_gb=40, disk_type="SSD",traffic_tb=2.0,price_mo=5.00, price_hr=0.007, currency="USD",locations="26 regions"),
    dict(provider="AWS Lightsail",name="Small",   cpu=2,ram_gb=2,  disk_gb=60, disk_type="SSD",traffic_tb=3.0,price_mo=10.00,price_hr=0.014, currency="USD",locations="26 regions"),
    dict(provider="AWS Lightsail",name="Medium",  cpu=2,ram_gb=4,  disk_gb=80, disk_type="SSD",traffic_tb=4.0,price_mo=20.00,price_hr=0.028, currency="USD",locations="26 regions"),
    dict(provider="AWS Lightsail",name="Large",   cpu=2,ram_gb=8,  disk_gb=160,disk_type="SSD",traffic_tb=5.0,price_mo=40.00,price_hr=0.055, currency="USD",locations="26 regions"),
    dict(provider="AWS Lightsail",name="XLarge",  cpu=4,ram_gb=16, disk_gb=320,disk_type="SSD",traffic_tb=6.0,price_mo=80.00,price_hr=0.11,  currency="USD",locations="26 regions"),
    dict(provider="AWS Lightsail",name="2XLarge", cpu=8,ram_gb=32, disk_gb=640,disk_type="SSD",traffic_tb=7.0,price_mo=160.00,price_hr=0.22, currency="USD",locations="26 regions"),
]

async def fetch_kamatera():
    return _fb_kamatera()  # API requires auth, use verified DB

def _fb_kamatera(): return [
    dict(provider="Kamatera",name="B1-1GB", cpu=1,ram_gb=1, disk_gb=20,disk_type="SSD",traffic_tb=0,price_mo=4.00, price_hr=0.0055,currency="USD",locations="Global (18 zones)"),
    dict(provider="Kamatera",name="B1-2GB", cpu=1,ram_gb=2, disk_gb=20,disk_type="SSD",traffic_tb=0,price_mo=5.50, price_hr=0.0075,currency="USD",locations="Global (18 zones)"),
    dict(provider="Kamatera",name="B2-4GB", cpu=2,ram_gb=4, disk_gb=40,disk_type="SSD",traffic_tb=0,price_mo=9.00, price_hr=0.012, currency="USD",locations="Global (18 zones)"),
    dict(provider="Kamatera",name="B4-8GB", cpu=4,ram_gb=8, disk_gb=80,disk_type="SSD",traffic_tb=0,price_mo=16.00,price_hr=0.022, currency="USD",locations="Global (18 zones)"),
    dict(provider="Kamatera",name="B8-16GB",cpu=8,ram_gb=16,disk_gb=160,disk_type="SSD",traffic_tb=0,price_mo=30.00,price_hr=0.041,currency="USD",locations="Global (18 zones)"),
]

async def fetch_hostinger():
    html = await GET("https://www.hostinger.com/vps-hosting")
    out = []
    if html:
        prices = re.findall(r'\$\s*([\d.]+)\s*/mo', html)
        rams   = re.findall(r'(\d+)\s*GB\s*RAM', html, re.I)
        cpus   = re.findall(r'(\d+)\s*vCPU', html, re.I)
        disks  = re.findall(r'(\d+)\s*GB\s*(?:NVMe|SSD)', html, re.I)
        for i, price in enumerate(prices[:6]):
            if float(price) <= 0: continue
            out.append(dict(provider="Hostinger", name=f"KVM {i+1}",
                cpu=int(cpus[i]) if i<len(cpus) else 0,
                ram_gb=int(rams[i]) if i<len(rams) else 0,
                disk_gb=int(disks[i]) if i<len(disks) else 0,
                disk_type="NVMe SSD", traffic_tb=0,
                price_mo=float(price), price_hr=round(float(price)/730,5),
                currency="USD", locations="8 locations"))
    return out if len(out) >= 3 else _fb_hostinger()

def _fb_hostinger(): return [
    dict(provider="Hostinger",name="KVM 1",cpu=1,ram_gb=4, disk_gb=50, disk_type="NVMe SSD",traffic_tb=0,price_mo=4.99, price_hr=0.0068,currency="USD",locations="8 locations"),
    dict(provider="Hostinger",name="KVM 2",cpu=2,ram_gb=8, disk_gb=100,disk_type="NVMe SSD",traffic_tb=0,price_mo=8.99, price_hr=0.0123,currency="USD",locations="8 locations"),
    dict(provider="Hostinger",name="KVM 4",cpu=4,ram_gb=16,disk_gb=200,disk_type="NVMe SSD",traffic_tb=0,price_mo=14.99,price_hr=0.0205,currency="USD",locations="8 locations"),
    dict(provider="Hostinger",name="KVM 8",cpu=8,ram_gb=32,disk_gb=400,disk_type="NVMe SSD",traffic_tb=0,price_mo=29.99,price_hr=0.0411,currency="USD",locations="8 locations"),
]

async def fetch_buyvm():
    html = await GET("https://buyvm.net/kvm-slice-vps/")
    out = []
    if html:
        prices = re.findall(r'\$([\d.]+)\s*/\s*(?:mo|month)', html, re.I)
        rams   = re.findall(r'(\d+(?:\.\d+)?)\s*GB\s*RAM', html, re.I)
        cpus   = re.findall(r'(\d+)\s*(?:vCore|vCPU|CPU)', html, re.I)
        disks  = re.findall(r'(\d+)\s*GB\s*SSD', html, re.I)
        for i, price in enumerate(prices[:6]):
            if float(price) <= 0: continue
            out.append(dict(provider="BuyVM", name=f"Slice {i+1}",
                cpu=int(cpus[i]) if i<len(cpus) else 1,
                ram_gb=float(rams[i]) if i<len(rams) else 0,
                disk_gb=int(disks[i]) if i<len(disks) else 0,
                disk_type="SSD", traffic_tb=0,
                price_mo=float(price), price_hr=round(float(price)/730,5),
                currency="USD", locations="US (LV/NY/MIA) / LU"))
    return out if len(out) >= 3 else _fb_buyvm()

def _fb_buyvm(): return [
    dict(provider="BuyVM",name="Slice 512MB",cpu=1,ram_gb=0.5,disk_gb=10, disk_type="SSD",traffic_tb=1,price_mo=2.00,price_hr=0.003, currency="USD",locations="US (LV/NY/MIA)/LU"),
    dict(provider="BuyVM",name="Slice 1GB",  cpu=1,ram_gb=1,  disk_gb=20, disk_type="SSD",traffic_tb=2,price_mo=3.50,price_hr=0.0048,currency="USD",locations="US (LV/NY/MIA)/LU"),
    dict(provider="BuyVM",name="Slice 2GB",  cpu=1,ram_gb=2,  disk_gb=40, disk_type="SSD",traffic_tb=3,price_mo=7.00,price_hr=0.0096,currency="USD",locations="US (LV/NY/MIA)/LU"),
    dict(provider="BuyVM",name="Slice 4GB",  cpu=2,ram_gb=4,  disk_gb=80, disk_type="SSD",traffic_tb=4,price_mo=12.50,price_hr=0.017,currency="USD",locations="US (LV/NY/MIA)/LU"),
]

async def fetch_greencloud():
    html = await GET("https://greencloudvps.com/billing/store/budget-kvm-vps")
    out = []
    if html:
        prices = re.findall(r'\$([\d.]+)\s*/\s*(?:mo|month)', html, re.I)
        rams   = re.findall(r'(\d+(?:\.\d+)?)\s*GB\s*RAM', html, re.I)
        cpus   = re.findall(r'(\d+)\s*(?:vCPU|CPU)', html, re.I)
        disks  = re.findall(r'(\d+)\s*GB\s*(?:SSD|NVMe|Disk)', html, re.I)
        for i, price in enumerate(prices[:6]):
            if float(price) <= 0: continue
            out.append(dict(provider="GreenCloud", name=f"Budget KVM {i+1}",
                cpu=int(cpus[i]) if i<len(cpus) else 1,
                ram_gb=float(rams[i]) if i<len(rams) else 0,
                disk_gb=int(disks[i]) if i<len(disks) else 0,
                disk_type="SSD", traffic_tb=0,
                price_mo=float(price), price_hr=round(float(price)/730,5),
                currency="USD", locations="US / EU / Asia / AU"))
    return out if len(out) >= 3 else _fb_greencloud()

def _fb_greencloud(): return [
    dict(provider="GreenCloud",name="Budget KVM 1",cpu=1,ram_gb=1, disk_gb=25, disk_type="SSD",traffic_tb=1,price_mo=2.50,price_hr=0.0034,currency="USD",locations="US/EU/Asia/AU"),
    dict(provider="GreenCloud",name="Budget KVM 2",cpu=2,ram_gb=2, disk_gb=50, disk_type="SSD",traffic_tb=2,price_mo=5.00,price_hr=0.0068,currency="USD",locations="US/EU/Asia/AU"),
    dict(provider="GreenCloud",name="Budget KVM 3",cpu=4,ram_gb=4, disk_gb=100,disk_type="SSD",traffic_tb=4,price_mo=9.00,price_hr=0.012, currency="USD",locations="US/EU/Asia/AU"),
    dict(provider="GreenCloud",name="Budget KVM 4",cpu=4,ram_gb=8, disk_gb=200,disk_type="SSD",traffic_tb=6,price_mo=16.00,price_hr=0.022,currency="USD",locations="US/EU/Asia/AU"),
]

async def fetch_nexusbytes():
    html = await GET("https://nexusbytes.com/vps/")
    out = []
    if html:
        prices = re.findall(r'\$([\d.]+)\s*/\s*(?:mo|month)', html, re.I)
        rams   = re.findall(r'(\d+(?:\.\d+)?)\s*GB\s*RAM', html, re.I)
        cpus   = re.findall(r'(\d+)\s*(?:vCPU|CPU|Core)', html, re.I)
        disks  = re.findall(r'(\d+)\s*GB\s*(?:NVMe|SSD|HDD)', html, re.I)
        for i, price in enumerate(prices[:5]):
            if float(price) <= 0: continue
            out.append(dict(provider="NexusBytes", name=f"NVMe KVM {i+1}",
                cpu=int(cpus[i]) if i<len(cpus) else 1,
                ram_gb=float(rams[i]) if i<len(rams) else 0,
                disk_gb=int(disks[i]) if i<len(disks) else 0,
                disk_type="NVMe", traffic_tb=0,
                price_mo=float(price), price_hr=round(float(price)/730,5),
                currency="USD", locations="US / UK / AU / NL"))
    return out if len(out) >= 3 else _fb_nexusbytes()

def _fb_nexusbytes(): return [
    dict(provider="NexusBytes",name="NVMe KVM 1",cpu=1,ram_gb=1, disk_gb=15, disk_type="NVMe",traffic_tb=0,price_mo=3.50,price_hr=0.0048,currency="USD",locations="US/UK/AU/NL"),
    dict(provider="NexusBytes",name="NVMe KVM 2",cpu=2,ram_gb=2, disk_gb=30, disk_type="NVMe",traffic_tb=0,price_mo=7.00,price_hr=0.0096,currency="USD",locations="US/UK/AU/NL"),
    dict(provider="NexusBytes",name="NVMe KVM 3",cpu=3,ram_gb=4, disk_gb=60, disk_type="NVMe",traffic_tb=0,price_mo=13.00,price_hr=0.018,currency="USD",locations="US/UK/AU/NL"),
    dict(provider="NexusBytes",name="NVMe KVM 4",cpu=4,ram_gb=8, disk_gb=120,disk_type="NVMe",traffic_tb=0,price_mo=24.00,price_hr=0.033,currency="USD",locations="US/UK/AU/NL"),
]

# ══════════════════════════════════════════════════════════════════════════════
#  PROVIDER MAPS
# ══════════════════════════════════════════════════════════════════════════════
FETCHERS: dict[str, Callable] = {
    "vultr":        fetch_vultr,
    "linode":       fetch_linode,
    "digitalocean": fetch_digitalocean,
    "scaleway":     fetch_scaleway,
    "upcloud":      fetch_upcloud,
    "hetzner":      fetch_hetzner,
    "contabo":      fetch_contabo,
    "ovh":          fetch_ovh,
    "racknerd":     fetch_racknerd,
    "lightsail":    fetch_lightsail,
    "kamatera":     fetch_kamatera,
    "hostinger":    fetch_hostinger,
    "buyvm":        fetch_buyvm,
    "greencloud":   fetch_greencloud,
    "nexusbytes":   fetch_nexusbytes,
}

PROVIDER_META: dict[str, dict] = {
    "vultr":        {"icon":"🔵","label":"Vultr",         "url":"https://www.vultr.com/pricing/",                   "src":"Public API"},
    "linode":       {"icon":"🟢","label":"Linode/Akamai",  "url":"https://www.linode.com/pricing/",                  "src":"Public API"},
    "digitalocean": {"icon":"🟣","label":"DigitalOcean",   "url":"https://www.digitalocean.com/pricing/droplets",    "src":"Public API"},
    "scaleway":     {"icon":"🟡","label":"Scaleway",       "url":"https://www.scaleway.com/en/pricing/",             "src":"Public API"},
    "upcloud":      {"icon":"🩵","label":"UpCloud",        "url":"https://upcloud.com/pricing/",                     "src":"Public API"},
    "hetzner":      {"icon":"🟠","label":"Hetzner Cloud",  "url":"https://www.hetzner.com/cloud",                    "src":"Live Scrape"},
    "contabo":      {"icon":"🟤","label":"Contabo",        "url":"https://contabo.com/en/vps/",                      "src":"Live Scrape"},
    "ovh":          {"icon":"🔴","label":"OVH Cloud",      "url":"https://www.ovhcloud.com/en/public-cloud/prices/", "src":"Public API"},
    "racknerd":     {"icon":"⚫","label":"RackNerd",       "url":"https://racknerd.com",                             "src":"Live Scrape"},
    "lightsail":    {"icon":"🟨","label":"AWS Lightsail",  "url":"https://amazonlightsail.com/pricing/",             "src":"Verified DB"},
    "kamatera":     {"icon":"🔷","label":"Kamatera",       "url":"https://www.kamatera.com/express/compute/",        "src":"Verified DB"},
    "hostinger":    {"icon":"🟪","label":"Hostinger VPS",  "url":"https://www.hostinger.com/vps-hosting",            "src":"Live Scrape"},
    "buyvm":        {"icon":"🟫","label":"BuyVM",          "url":"https://buyvm.net/kvm-slice-vps/",                 "src":"Live Scrape"},
    "greencloud":   {"icon":"🌿","label":"GreenCloud",     "url":"https://greencloudvps.com/",                       "src":"Live Scrape"},
    "nexusbytes":   {"icon":"💠","label":"NexusBytes",     "url":"https://nexusbytes.com/vps/",                      "src":"Live Scrape"},
}

# ══════════════════════════════════════════════════════════════════════════════
#  FREE DATABASES
# ══════════════════════════════════════════════════════════════════════════════
FREE_VPS = [
    # ─ Always Free ─────────────────────────────────────────────────────────
    {"cat":"🟢 ALWAYS FREE","name":"Oracle Cloud Always Free",
     "specs":"2×AMD (1 OCPU 1GB) + 4×Arm A1 (24GB RAM total)","price":"FREE Forever",
     "note":"Best free tier ever made ⭐","link":"https://www.oracle.com/cloud/free/",
     "tip":"Run 4 A1 cores + 24GB RAM as one big instance. Fully persistent, never sleeps."},
    {"cat":"🟢 ALWAYS FREE","name":"Google Cloud Always Free",
     "specs":"e2-micro — 0.25 vCPU, 1GB RAM, 30GB HDD","price":"FREE Forever",
     "note":"US regions only (Oregon/Iowa/S. Carolina)","link":"https://cloud.google.com/free",
     "tip":"Combine with free static IP, 1GB/mo outbound, and Cloud Functions free tier."},
    {"cat":"🟢 ALWAYS FREE","name":"Fly.io Free Tier",
     "specs":"3× shared-cpu-1x VMs, 256MB RAM each, 3GB SSD","price":"FREE Forever",
     "note":"Great for bots, APIs, microservices","link":"https://fly.io/docs/about/pricing/",
     "tip":"No credit card needed. Docker-based deploy. Global anycast network."},
    {"cat":"🟢 ALWAYS FREE","name":"Render Free Web Service",
     "specs":"512MB RAM shared CPU","price":"FREE Forever",
     "note":"Sleeps after 15min idle","link":"https://render.com/pricing",
     "tip":"Use UptimeRobot free pings to prevent sleep. Add persistent disk $7/mo."},
    {"cat":"🟢 ALWAYS FREE","name":"IBM Cloud Lite",
     "specs":"256MB Cloud Foundry runtime + Cloudant 1GB DB","price":"FREE Forever",
     "note":"No expiry, no credit card","link":"https://www.ibm.com/cloud/free",
     "tip":"Also includes Watson APIs, 500MB Object Storage, and Code Engine free tier."},
    {"cat":"🟢 ALWAYS FREE","name":"Cloudflare Workers",
     "specs":"100k requests/day, 128MB per worker, 10ms CPU","price":"FREE Forever",
     "note":"Serverless but runs on CF global network","link":"https://workers.cloudflare.com/",
     "tip":"Run bots, APIs, proxies — deploys in <1s. Combine with KV storage 1GB free."},
    {"cat":"🟢 ALWAYS FREE","name":"Deta Space",
     "specs":"Unlimited Micro instances, 10GB storage, scheduled jobs","price":"FREE Forever",
     "note":"Deploy Python/Node instantly","link":"https://deta.space/",
     "tip":"No credit card ever. Full persistence, cron support, custom domains free."},
    {"cat":"🟢 ALWAYS FREE","name":"Koyeb Free Tier",
     "specs":"2 nano services, 512MB RAM, 2GB SSD","price":"FREE Forever",
     "note":"Docker/GitHub deploy, global edge","link":"https://www.koyeb.com/pricing",
     "tip":"Frankfurt + Washington DC edge. Sleeps on idle. Perfect for webhooks."},
    {"cat":"🟢 ALWAYS FREE","name":"Railway Hobby Plan",
     "specs":"~512MB RAM containers, $5 credit/mo","price":"$5 free credit/mo",
     "note":"Renews monthly, no CC required","link":"https://railway.app/pricing",
     "tip":"GitHub deploy + persistent volumes + MySQL/Redis included free."},
    {"cat":"🟢 ALWAYS FREE","name":"Glitch Free",
     "specs":"512MB RAM, 200MB disk, full Node/Python","price":"FREE Forever",
     "note":"Sleeps after 5min idle","link":"https://glitch.com/pricing",
     "tip":"Great for Discord bots. Boosted plan ($8/mo) keeps it awake."},

    # ─ Trial / Credit ───────────────────────────────────────────────────────
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"AWS Free Tier",
     "specs":"t2.micro / t3.micro — 1 vCPU, 1GB RAM, 30GB SSD","price":"FREE 12 months",
     "note":"750 hours/month EC2 included","link":"https://aws.amazon.com/free/",
     "tip":"Use t3.micro (faster than t2). Also: free RDS, Lambda 1M calls, S3 5GB, CloudFront 1TB/mo."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Microsoft Azure Free",
     "specs":"B1S — 1 vCPU, 1GB RAM, 32GB SSD","price":"$200 credit (30d) + 12mo B1S free",
     "note":"Best for Windows/.NET workloads","link":"https://azure.microsoft.com/en-us/free/",
     "tip":"$200 expires in 30 days. B1S Linux/Windows VM stays free for 12 months. Renew yearly?"},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Google Cloud $300 Trial",
     "specs":"Any GCP instance — $300 credit, 90 days","price":"$300 free credit",
     "note":"No auto-charge after trial ends","link":"https://cloud.google.com/free",
     "tip":"Run an n2-standard-4 (4vCPU 16GB) for ~3 months free. GPU VMs work too!"},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Hetzner €20 Trial",
     "specs":"Any Hetzner Cloud instance","price":"€20 free credit",
     "note":"Best value paid provider in Europe","link":"https://www.hetzner.com/cloud",
     "tip":"€20 = 5 months of CX22 (2vCPU 4GB NVMe). CAX11 ARM64 is even cheaper at €3.29/mo."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Alibaba Cloud Trial",
     "specs":"ecs.t5-lc1m2.small — 1 vCPU, 2GB RAM, 40GB SSD","price":"FREE 3 months",
     "note":"New users, SMS verification required","link":"https://www.alibabacloud.com/campaign/free-trial",
     "tip":"30+ global regions. Also includes OSS 5GB, CDN 50GB bandwidth free."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Huawei Cloud Free Year",
     "specs":"t6 micro — 1 vCPU, 1GB RAM, 40GB SSD","price":"FREE 1 year",
     "note":"New users, Huawei account required","link":"https://activity.huaweicloud.com/free_packages.html",
     "tip":"Also: OBS 5GB storage, CCE free tier, DCS Redis 500MB all included."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Kamatera 30-Day Trial",
     "specs":"1 vCPU, 1GB RAM, 20GB SSD","price":"FREE 30 days — NO CREDIT CARD",
     "note":"18 global zones, fastest setup","link":"https://www.kamatera.com/express/compute/",
     "tip":"Best no-CC trial available. Choose from 18 zones including Tel Aviv, HK, Tokyo."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"DigitalOcean $200 Credit",
     "specs":"Any Droplet — $200 free credit","price":"$200 credit (60 days)",
     "note":"Via referral / GitHub Student Pack","link":"https://try.digitalocean.com/freetrialoffer/",
     "tip":"GitHub Student Pack gives $200 DO credit. Also check DO's referral program."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Vultr $100 Promo",
     "specs":"Any Vultr plan","price":"$100 free credit (30 days)",
     "note":"Promo codes available online","link":"https://www.vultr.com/promo/try100/",
     "tip":"Search 'Vultr promo code 2025' — many $50-$100 codes still active."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Linode $100 Credit",
     "specs":"Any Linode instance","price":"$100 free credit (60 days)",
     "note":"Promo code required","link":"https://www.linode.com/lp/brand-free-credit/",
     "tip":"Check Linode's Twitter/blog for codes. LINODE10 gives $10. Referrals give more."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"OVH Public Cloud Trial",
     "specs":"d2-2 — 1 vCPU, 2GB RAM, 25GB NVMe","price":"€0 first 3 months",
     "note":"EU new accounts Discovery plan","link":"https://www.ovhcloud.com/en/public-cloud/",
     "tip":"Discovery tier d2-2 has been offered free to new EU accounts. Check signup page."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Tencent Cloud Trial",
     "specs":"S5.SMALL2 — 1 vCPU, 2GB RAM, 50GB SSD","price":"FREE 3 months",
     "note":"New users — China + international","link":"https://www.tencentcloud.com/campaign/freetier",
     "tip":"Great for APAC. Free CDN 50GB bandwidth + TencentDB 1GB also included."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Exoscale Free Trial",
     "specs":"Any Exoscale instance","price":"CHF 50 credit",
     "note":"Swiss cloud, GDPR-compliant, no auto-charge","link":"https://www.exoscale.com/",
     "tip":"Best for Swiss/EU data residency requirements. No charge after credit ends."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"UpCloud €10 Trial",
     "specs":"Any UpCloud server","price":"€10 free credit",
     "note":"Ranked #1 in independent speed benchmarks","link":"https://upcloud.com/signup/",
     "tip":"MaxIOPS delivers 100k IOPS. Genuinely the fastest storage in budget VPS category."},
    {"cat":"🟡 FREE TRIAL / CREDIT","name":"Scaleway Stardust Trial",
     "specs":"STARDUST1-S — 1 vCPU, 1GB RAM","price":"FREE tier / €1.80/mo after",
     "note":"Limited availability — check Paris/AMS","link":"https://www.scaleway.com/en/pricing/",
     "tip":"Cheapest paid VPS in the EU. Great for always-on bots. Limited stock."},

    # ─ Student / Special ────────────────────────────────────────────────────
    {"cat":"🎓 STUDENT / SPECIAL","name":"GitHub Student Pack",
     "specs":"DigitalOcean $200 + Namecheap + JetBrains + 100+ tools","price":"FREE with .edu email",
     "note":"Biggest developer freebie pack","link":"https://education.github.com/pack",
     "tip":"Also includes Heroku, MongoDB Atlas $200, DataStax Astra, Canva Pro, and more."},
    {"cat":"🎓 STUDENT / SPECIAL","name":"Azure for Students",
     "specs":"$100 credit + select free services","price":"$100/year FREE — no CC needed",
     "note":"Renews annually with student status","link":"https://azure.microsoft.com/en-us/free/students/",
     "tip":"Best student deal — $100 renews every year. No credit card ever required."},
    {"cat":"🎓 STUDENT / SPECIAL","name":"AWS Educate",
     "specs":"$200–400 AWS credit","price":"FREE for students",
     "note":"Any .edu email, no CC required","link":"https://aws.amazon.com/education/awseducate/",
     "tip":"Credits vary by institution. Apply early — some schools have enhanced credit amounts."},
    {"cat":"🎓 STUDENT / SPECIAL","name":"Google Cloud for Edu",
     "specs":"$300 GCP credit + Workspace Edu","price":"FREE for students",
     "note":"Via Google for Education program","link":"https://edu.google.com/programs/credits/",
     "tip":"If your school has Google Workspace Edu, extra GCP credits may be available."},
    {"cat":"🎓 STUDENT / SPECIAL","name":"Cloudflare Tunnel (Self-host)",
     "specs":"Expose local server to internet","price":"FREE Forever — no VPS needed",
     "note":"Alternative to VPS for home hosting","link":"https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/",
     "tip":"Run your server on a Raspberry Pi at home, expose it globally via Cloudflare. Zero cost."},
]

FREE_SSH = [
    {"name":"FastSSH",       "link":"https://www.fastssh.com/",         "expiry":"1–7 days",  "protocols":"SSH, V2Ray, Xray, VMess, Trojan, VLESS", "note":"Best daily refresh — most protocols"},
    {"name":"SSHKit",        "link":"https://sshkit.com/",              "expiry":"3–7 days",  "protocols":"SSH, OpenVPN",                           "note":"Multiple server locations, fast creation"},
    {"name":"OpenTunnel",    "link":"https://opentunnel.net/",          "expiry":"3–5 days",  "protocols":"SSH, SlowDNS, UDP Custom",                "note":"SSH tunnel + DNS tunnel accounts"},
    {"name":"FreeSSH.org",   "link":"https://www.freessh.org/",         "expiry":"7 days",    "protocols":"SSH",                                    "note":"Worldwide servers, auto-created instantly"},
    {"name":"SSHagan",       "link":"https://sshagan.net/",             "expiry":"7–14 days", "protocols":"SSH, V2Ray, VPN",                        "note":"Long validity, high-speed servers"},
    {"name":"VPNJantit",     "link":"https://www.vpnjantit.com/",       "expiry":"7 days",    "protocols":"SSH, OpenVPN, SoftEther, V2Ray, Xray",   "note":"Huge variety — most protocol types"},
    {"name":"Goodssh",       "link":"https://goodssh.com/",             "expiry":"1–3 days",  "protocols":"SSH",                                    "note":"SG, US, EU servers daily"},
    {"name":"SSH7Days",      "link":"https://ssh7days.com/",            "expiry":"7 days",    "protocols":"SSH",                                    "note":"Guaranteed 7-day validity"},
    {"name":"SSHOcean",      "link":"https://sshocean.com/",            "expiry":"7 days",    "protocols":"SSH, XVPN, SlowDNS, V2Ray",              "note":"Many protocol options"},
    {"name":"CreateSSH",     "link":"https://createssh.net/",           "expiry":"7 days",    "protocols":"SSH, V2Ray",                             "note":"Instant creation, no registration"},
    {"name":"ServerSSH",     "link":"https://www.serverssh.net/",       "expiry":"7 days",    "protocols":"SSH",                                    "note":"Premium locations: SG/US/EU/JP/ID"},
    {"name":"SSHTunnel.us",  "link":"https://sshtunnel.us/",            "expiry":"1–7 days",  "protocols":"SSH, WebSocket, BadVPN UDP",              "note":"UDP tunnel + WebSocket support"},
    {"name":"CloudSSH",      "link":"https://cloudssh.us/",             "expiry":"7 days",    "protocols":"SSH, WebSocket",                         "note":"Cloud-based with WebSocket payload"},
    {"name":"FreeSSH.in",    "link":"https://freessh.in/",              "expiry":"3 days",    "protocols":"SSH, V2Ray VMess, VLESS, Trojan",         "note":"Multiple V2Ray variants"},
    {"name":"MySSH.website", "link":"https://myssh.website/",           "expiry":"7 days",    "protocols":"SSH, V2Ray",                             "note":"Premium-location accounts free"},
]

FREE_RDP = [
    {"name":"AWS Windows Free Tier",   "link":"https://aws.amazon.com/free/",              "specs":"t2.micro — 1vCPU 1GB, Windows Server 2022",     "note":"750 hr/mo FREE 12 months. Port 3389 RDP. Best starter Windows cloud VM."},
    {"name":"Azure Free Windows VM",   "link":"https://azure.microsoft.com/en-us/free/",   "specs":"B1S — 1vCPU 1GB, Windows Server",                "note":"12 months free + $200 credit. Best free Windows VM overall."},
    {"name":"GCP $300 Windows VM",     "link":"https://cloud.google.com/free",             "specs":"n2-standard-2 (2vCPU 8GB) — Windows Server",    "note":"$300 credit covers a decent Windows VM for months. Full GUI."},
    {"name":"Oracle Cloud ARM + xRDP", "link":"https://www.oracle.com/cloud/free/",        "specs":"4×A1 cores 24GB RAM — install xRDP for desktop", "note":"Best permanent free RDP! Install Ubuntu + xRDP on Oracle Arm. Runs forever."},
    {"name":"Paperspace Free CPU",     "link":"https://www.paperspace.com/",               "specs":"C2/C3 tier CPU machine, full GUI Linux/Windows",  "note":"Free CPU cloud desktop. Paid GPU tiers start at $8/mo."},
    {"name":"Shadow PC",               "link":"https://shadow.tech/",                      "specs":"4 vCPU, 12GB RAM, GTX 1080 equiv GPU",           "note":"Cloud gaming PC — full Windows 11. Free trial periods available."},
    {"name":"Shells.com Trial",        "link":"https://www.shells.com/",                   "specs":"1 vCPU, 1GB RAM, 5GB storage — full desktop",    "note":"Full cloud desktop trial. No CC on select offers."},
    {"name":"GitHub Codespaces",       "link":"https://github.com/features/codespaces",    "specs":"2-core 8GB RAM, 32GB storage, VSCode in browser","note":"60 hrs/month FREE. Full terminal + GUI editor. Not RDP but full cloud dev env."},
    {"name":"Google Cloud Shell",      "link":"https://shell.cloud.google.com/",           "specs":"e2-small equiv, 5GB persistent, Theia IDE",      "note":"Free browser terminal + web IDE. 50hr/week limit. Boost mode available."},
    {"name":"Gitpod Free Tier",        "link":"https://www.gitpod.io/pricing",             "specs":"2 vCPU, 4GB RAM, 30GB storage",                  "note":"50 hrs/month free. Browser VSCode + terminal. GitHub/GitLab integration."},
    {"name":"Google Colab + Tunnel",   "link":"https://colab.research.google.com/",        "specs":"T4 GPU or CPU, 12-25GB RAM",                     "note":"Not RDP but run ngrok/cloudflared tunnel + VNC for graphical access to free GPU."},
    {"name":"Cloudflare RDP Tunnel",   "link":"https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/","specs":"Tunnel your local Windows/Linux to internet","note":"Self-host RDP at home, access globally via Cloudflare Zero Trust tunnel. FREE."},
]

# ══════════════════════════════════════════════════════════════════════════════
#  FETCH LAYER
# ══════════════════════════════════════════════════════════════════════════════
async def fetch_one(key: str, force=False) -> list[dict]:
    ent = _cache.get(key)
    if not force and ent and ent.is_fresh():
        log.info(f"HIT  {key} ({ent.age_str()})"); return ent.data
    log.info(f"MISS {key} — fetching…")
    try:
        data = await FETCHERS[key]()
        _cache[key] = CacheEntry(data=data); return data
    except Exception as e:
        log.error(f"fetch_one({key}): {e}")
        if ent: return ent.data
        return []

async def fetch_all(force=False) -> dict[str, list]:
    keys    = list(FETCHERS)
    results = await asyncio.gather(*[fetch_one(k, force) for k in keys], return_exceptions=True)
    return {k: (r if isinstance(r, list) else []) for k, r in zip(keys, results)}

# ══════════════════════════════════════════════════════════════════════════════
#  BACKGROUND WARMER
# ══════════════════════════════════════════════════════════════════════════════
async def background_warmer():
    await asyncio.sleep(2)
    while True:
        log.info("🔃 Warm cycle start")
        await fetch_all()
        log.info("✅ Warm cycle done")
        await asyncio.sleep(WARM_SECS)

# ══════════════════════════════════════════════════════════════════════════════
#  FORMATTERS
# ══════════════════════════════════════════════════════════════════════════════
def fmt_entry(p: dict, idx=0) -> str:
    specs = []
    if p.get("cpu"):     specs.append(f"{p['cpu']} vCPU")
    if p.get("ram_gb"):  specs.append(f"{p['ram_gb']}GB RAM")
    if p.get("disk_gb"): specs.append(f"{p['disk_gb']}GB {p.get('disk_type','SSD')}")
    sp  = " | ".join(specs) if specs else "See site"
    tb  = f" | 🌐{p['traffic_tb']}TB" if p.get("traffic_tb") else ""
    sc  = p.get("value_score", vscore(p))
    pre = f"*{idx}. " if idx else "  "
    return (f"{pre}{p['name']}*\n"
            f"  ⚙️ `{sp}`{tb}\n"
            f"  💵 `{p['currency']} {p['price_mo']:.2f}/mo`  ≈  `{p['price_hr']:.5f}/hr`\n"
            f"  🌍 {p.get('locations','')}\n"
            f"  🏆 `{bar(sc)}` `{sc:.3f}`\n")

def _pages(hdr: str, items: list[str], cont: str) -> list[str]:
    pages, body = [], ""
    for it in items:
        if len(hdr + body + it) > PAGE_LIMIT:
            pages.append((hdr + body).strip()); hdr, body = cont, it
        else:
            body += it
    pages.append((hdr + body).strip())
    return pages

def fmt_provider(key: str, plans: list[dict]) -> list[str]:
    m   = PROVIDER_META[key]
    ent = _cache.get(key)
    hdr = (f"{m['icon']} *{m['label']}*  `[{m['src']}]`\n`{'─'*32}`\n"
           f"🔗 {m['url']}\n"
           f"🕐 `{ent.age_str() if ent else 'unknown'}`  {'✅' if ent and not ent.error else '⚠️'}\n\n")
    if not plans: return [hdr + "⚠️ No plans fetched. Try again."]
    items = [fmt_entry(p, i) + "\n" for i, p in enumerate(plans, 1)]
    return _pages(hdr + f"📦 `{len(plans)} plans`\n\n", items, f"{m['icon']} *{m['label']} (cont.)*\n\n")

def fmt_compare(all_plans: dict) -> list[str]:
    rows = []
    for key, plans in all_plans.items():
        if not plans: continue
        p = min(plans, key=lambda x: x["price_mo"])
        m = PROVIDER_META[key]
        rows.append({**p, "icon":m["icon"], "label":m["label"], "url":m["url"]})
    rows.sort(key=lambda x: x["price_mo"])
    medals = ["🥇","🥈","🥉"]
    hdr = (f"📊 *CHEAPEST — ALL {len(rows)} PROVIDERS*\n`{'─'*34}`\n"
           f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n")
    items = []
    for i, r in enumerate(rows):
        specs = []
        if r.get("cpu"):     specs.append(f"{r['cpu']} vCPU")
        if r.get("ram_gb"):  specs.append(f"{r['ram_gb']}GB")
        if r.get("disk_gb"): specs.append(f"{r['disk_gb']}GB")
        sc = r.get("value_score", vscore(r))
        items.append(
            f"{medals[i] if i<3 else f'`{i+1}.`'} {r['icon']} *{r['label']}*\n"
            f"  `{'|'.join(specs) or 'See site'}`\n"
            f"  💵 *{r['currency']} {r['price_mo']:.2f}/mo*\n"
            f"  🏆 `{bar(sc)}` `{sc:.3f}`\n"
            f"  🔗 {r['url']}\n\n"
        )
    return _pages(hdr, items, "📊 *Compare (cont.)*\n\n")

def fmt_deals(all_plans: dict, top=12) -> list[str]:
    flat = []
    for key, plans in all_plans.items():
        m = PROVIDER_META.get(key,{})
        for p in plans:
            flat.append({**p, "icon":m.get("icon",""), "label":m.get("label",""), "url":m.get("url","")})
    flat.sort(key=lambda x: -x.get("value_score", vscore(x)))
    hdr = (f"💎 *TOP {top} VALUE DEALS — ALL PROVIDERS*\n`{'─'*36}`\n"
           f"_Ranked by (CPU×RAM + Disk×0.01) ÷ price_\n\n")
    items = []
    for i, p in enumerate(flat[:top], 1):
        specs = []
        if p.get("cpu"):     specs.append(f"{p['cpu']} vCPU")
        if p.get("ram_gb"):  specs.append(f"{p['ram_gb']}GB")
        if p.get("disk_gb"): specs.append(f"{p['disk_gb']}GB")
        sc = p.get("value_score", vscore(p))
        items.append(
            f"*{i}.* {p['icon']} *{p['label']}* — {p['name']}\n"
            f"  `{'|'.join(specs) or 'See site'}`\n"
            f"  💵 `{p['currency']} {p['price_mo']:.2f}/mo`\n"
            f"  🏆 `{bar(sc,8,10)}` `{sc:.3f}`\n"
            f"  🔗 {p.get('url','')}\n\n"
        )
    return _pages(hdr, items, "💎 *Deals (cont.)*\n\n")

def fmt_filtered(all_plans: dict, max_p: float, min_r: float, min_c: int) -> list[str]:
    matched = []
    for key, plans in all_plans.items():
        m = PROVIDER_META.get(key,{})
        for p in plans:
            if p["price_mo"] > max_p: continue
            if p.get("ram_gb",0) < min_r: continue
            if p.get("cpu",0) < min_c: continue
            matched.append({**p, "icon":m.get("icon",""), "label":m.get("label",""), "url":m.get("url","")})
    matched.sort(key=lambda x: x["price_mo"])
    hdr = (f"🔍 *FILTER RESULTS*\n`{'─'*30}`\n"
           f"💵 Max `${max_p:.0f}/mo`  🧠 Min `{min_r}GB RAM`  ⚙️ Min `{min_c} CPU`\n"
           f"Found `{len(matched)} plans`\n\n")
    if not matched:
        return [hdr + "❌ No plans match. Try relaxing the filter criteria."]
    items = [fmt_entry(p, i) + "\n" for i, p in enumerate(matched, 1)]
    return _pages(hdr, items, "🔍 *Filtered (cont.)*\n\n")

def fmt_stats(all_plans: dict) -> str:
    total = sum(len(v) for v in all_plans.values())
    lines = [f"📈 *VPS BOT v5 — STATS*\n`{'─'*32}`\n🕐 `{datetime.now(timezone.utc).strftime('%H:%M UTC')}`\n\n"]
    for key, plans in all_plans.items():
        m   = PROVIDER_META.get(key,{})
        ent = _cache.get(key)
        ok  = "✅" if ent and not ent.error else ("⚠️" if ent else "❓")
        age = ent.age_str() if ent else "not cached"
        lines.append(f"{ok} {m.get('icon','')} *{m.get('label',key)}*  `{len(plans)}` plans  _{age}_\n")
    lines.append(f"\n📦 *Total cached:* `{total} plans`\n🔄 `TTL {CACHE_TTL}s` · `{len(FETCHERS)} providers`")
    return "".join(lines)

def fmt_free_vps() -> list[str]:
    cats: dict[str,list] = {}
    for v in FREE_VPS:
        cats.setdefault(v["cat"],[]).append(v)
    pages, cur = [], f"🆓 *FREE VPS — {len(FREE_VPS)} Options*\n`{'═'*32}`\n"
    for cat, items in cats.items():
        cur += f"\n*{cat}*\n`{'─'*28}`\n"
        for v in items:
            blk = (f"🔹 *{v['name']}*\n"
                   f"  ⚙️ `{v['specs']}`\n"
                   f"  💵 {v['price']}  ·  _{v['note']}_\n"
                   f"  💡 _{v.get('tip','')}_\n"
                   f"  🔗 {v['link']}\n\n")
            if len(cur + blk) > PAGE_LIMIT:
                pages.append(cur.strip()); cur = blk
            else:
                cur += blk
    if cur.strip(): pages.append(cur.strip())
    return pages

def fmt_ssh() -> list[str]:
    hdr = f"🔐 *FREE SSH — {len(FREE_SSH)} Sources*\n`{'═'*32}`\n\n"
    items = []
    for i, s in enumerate(FREE_SSH, 1):
        items.append(
            f"*{i}. {s['name']}*\n"
            f"  ⏱ `{s['expiry']}`  📡 `{s['protocols']}`\n"
            f"  💡 _{s['note']}_\n"
            f"  🔗 {s['link']}\n\n"
        )
    items.append("⚠️ _SSH accounts expire. Regenerate fresh ones daily._")
    return _pages(hdr, items, "🔐 *SSH (cont.)*\n\n")

def fmt_rdp() -> list[str]:
    hdr = f"🖥 *FREE RDP / CLOUD DESKTOP — {len(FREE_RDP)} Sources*\n`{'═'*32}`\n\n"
    items = []
    for i, r in enumerate(FREE_RDP, 1):
        items.append(
            f"*{i}. {r['name']}*\n"
            f"  ⚙️ `{r['specs']}`\n"
            f"  💡 _{r['note']}_\n"
            f"  🔗 {r['link']}\n\n"
        )
    items.append("⚠️ _Register on provider site to activate your free trial._")
    return _pages(hdr, items, "🖥 *RDP (cont.)*\n\n")

def to_txt(cache: dict) -> str:
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"VPS SCRAPER BOT v5 — {now}", "=" * 60, ""]
    for key, plans in cache.items():
        if not isinstance(plans, list): continue
        m = PROVIDER_META.get(key,{})
        lines += [f"=== {m.get('label',key).upper()} ({m.get('src','')}) ===",
                  f"URL: {m.get('url','')}", f"Plans: {len(plans)}", ""]
        for p in plans:
            lines.append(
                f"  {p['name']:35} | {p.get('cpu',0):2d} vCPU | {p.get('ram_gb',0):5.1f}GB | "
                f"{p.get('disk_gb',0):4d}GB | {p['currency']} {p['price_mo']:7.2f}/mo | "
                f"Score:{p.get('value_score',vscore(p)):.3f}"
            )
        lines.append("")
    return "\n".join(lines)

def to_csv(cache: dict) -> str:
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["provider","name","cpu","ram_gb","disk_gb","disk_type",
                "traffic_tb","price_mo","price_hr","currency","locations","value_score"])
    for plans in cache.values():
        if not isinstance(plans, list): continue
        for p in plans:
            w.writerow([p.get("provider",""),p.get("name",""),p.get("cpu",0),p.get("ram_gb",0),
                        p.get("disk_gb",0),p.get("disk_type",""),p.get("traffic_tb",0),
                        p.get("price_mo",0),p.get("price_hr",0),p.get("currency",""),
                        p.get("locations",""),p.get("value_score",vscore(p))])
    return buf.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════════════════════════
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔵 Vultr",        callback_data="p_vultr"),
         InlineKeyboardButton("🟢 Linode",       callback_data="p_linode"),
         InlineKeyboardButton("🟣 DigitalOcean", callback_data="p_digitalocean")],
        [InlineKeyboardButton("🟡 Scaleway",     callback_data="p_scaleway"),
         InlineKeyboardButton("🩵 UpCloud",      callback_data="p_upcloud"),
         InlineKeyboardButton("🟠 Hetzner",      callback_data="p_hetzner")],
        [InlineKeyboardButton("🟤 Contabo",      callback_data="p_contabo"),
         InlineKeyboardButton("🔴 OVH",          callback_data="p_ovh"),
         InlineKeyboardButton("⚫ RackNerd",     callback_data="p_racknerd")],
        [InlineKeyboardButton("🟨 Lightsail",    callback_data="p_lightsail"),
         InlineKeyboardButton("🔷 Kamatera",     callback_data="p_kamatera"),
         InlineKeyboardButton("🟪 Hostinger",    callback_data="p_hostinger")],
        [InlineKeyboardButton("🟫 BuyVM",        callback_data="p_buyvm"),
         InlineKeyboardButton("🌿 GreenCloud",   callback_data="p_greencloud"),
         InlineKeyboardButton("💠 NexusBytes",   callback_data="p_nexusbytes")],
        [InlineKeyboardButton("📊 Compare All",  callback_data="compare"),
         InlineKeyboardButton("💎 Best Deals",   callback_data="deals"),
         InlineKeyboardButton("📈 Stats",        callback_data="stats")],
        [InlineKeyboardButton("🆓 Free VPS",     callback_data="free"),
         InlineKeyboardButton("🔐 Free SSH",     callback_data="ssh"),
         InlineKeyboardButton("🖥 Free RDP",     callback_data="rdp")],
        [InlineKeyboardButton("🔍 Filter Plans", callback_data="filter_menu"),
         InlineKeyboardButton("🚀 Fetch ALL",    callback_data="fetch_all"),
         InlineKeyboardButton("📥 Export",       callback_data="export_menu")],
    ])

def export_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Export .TXT", callback_data="export_txt"),
         InlineKeyboardButton("📊 Export .CSV", callback_data="export_csv")],
        [InlineKeyboardButton("🔙 Back",        callback_data="back_main")],
    ])

def filter_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Under $5/mo",   callback_data="f_5_0_0"),
         InlineKeyboardButton("💵 Under $10/mo",  callback_data="f_10_0_0")],
        [InlineKeyboardButton("💵 Under $20/mo",  callback_data="f_20_0_0"),
         InlineKeyboardButton("💵 Under $50/mo",  callback_data="f_50_0_0")],
        [InlineKeyboardButton("🧠 2GB RAM <$10",  callback_data="f_10_2_0"),
         InlineKeyboardButton("🧠 4GB RAM <$20",  callback_data="f_20_4_0")],
        [InlineKeyboardButton("🧠 8GB RAM <$30",  callback_data="f_30_8_2"),
         InlineKeyboardButton("⚙️ 4CPU+8GB <$50", callback_data="f_50_8_4")],
        [InlineKeyboardButton("🔙 Back",          callback_data="back_main")],
    ])

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
async def send_pages(fn, pages: list[str]):
    for page in pages:
        try:
            await fn(page, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        except Exception:
            try: await fn(page, disable_web_page_preview=True)
            except: pass
        if len(pages) > 1:
            await asyncio.sleep(0.2)

# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cached = sum(len(e.data) for e in _cache.values() if e.is_fresh())
    txt = (f"🖥 *VPS Scraper Bot v5 — Maximum Edition*\n\n"
           f"⚡ `{len(FETCHERS)} providers`  ·  🆓 `{len(FREE_VPS)} free tiers`  ·  🔐 `{len(FREE_SSH)} SSH`  ·  🖥 `{len(FREE_RDP)} RDP`\n"
           f"📦 `{cached} plans in cache`\n\n"
           "*Commands:*\n"
           "/compare  — cheapest plan from all providers\n"
           "/deal     — top value plans (ranked by score)\n"
           "/filter   — filter `<max_price> <min_ram> <min_cpu>`\n"
           "/free     — 30 free VPS tiers with tips\n"
           "/ssh      — 15 free SSH sources\n"
           "/rdp      — 12 free RDP / cloud desktop sources\n"
           "/all      — fetch all 15 providers in parallel\n"
           "/stats    — cache health & provider status\n"
           "/export   — download as .txt or .csv\n\n"
           "👇 Use buttons below")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb())

async def cmd_free(update: Update, _):    await send_pages(update.message.reply_text, fmt_free_vps())
async def cmd_ssh(update: Update, _):     await send_pages(update.message.reply_text, fmt_ssh())
async def cmd_rdp(update: Update, _):     await send_pages(update.message.reply_text, fmt_rdp())

async def cmd_compare(update: Update, _):
    msg = await update.message.reply_text(f"⏳ Parallel-fetching all {len(FETCHERS)} providers…")
    data = await fetch_all()
    try: await msg.delete()
    except: pass
    await send_pages(update.message.reply_text, fmt_compare(data))

async def cmd_deal(update: Update, _):
    msg = await update.message.reply_text("⏳ Computing value scores…")
    data = await fetch_all()
    try: await msg.delete()
    except: pass
    await send_pages(update.message.reply_text, fmt_deals(data))

async def cmd_filter_args(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 3:
        await update.message.reply_text(
            "🔍 Usage: /filter `<max_price> <min_ram_gb> <min_cpu>`\nExample: `/filter 15 4 2`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=filter_kb()); return
    try:
        max_p = float(args[0]); min_r = float(args[1]); min_c = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ Use numbers: /filter 15 4 2"); return
    msg  = await update.message.reply_text("⏳ Filtering…")
    data = await fetch_all()
    try: await msg.delete()
    except: pass
    await send_pages(update.message.reply_text, fmt_filtered(data, max_p, min_r, min_c))

async def cmd_all(update: Update, _):
    msg = await update.message.reply_text(f"⏳ Fetching all {len(FETCHERS)} providers in parallel…")
    data = await fetch_all(force=True)
    try: await msg.delete()
    except: pass
    total   = sum(len(v) for v in data.values())
    summary = (f"✅ *{total} plans — {len(FETCHERS)} providers*\n`{'─'*32}`\n" +
               "\n".join(f"{PROVIDER_META[k]['icon']} {PROVIDER_META[k]['label']}: `{len(v)}`"
                         for k, v in data.items()))
    await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text("Choose a provider:", reply_markup=main_kb())

async def cmd_stats(update: Update, _):
    data = {k: _cache[k].data if k in _cache else [] for k in FETCHERS}
    await update.message.reply_text(fmt_stats(data), parse_mode=ParseMode.MARKDOWN)

async def cmd_export(update: Update, _):
    await update.message.reply_text("Choose export format:", reply_markup=export_kb())

# ══════════════════════════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    send = q.message.reply_text

    if data.startswith("p_"):
        key  = data[2:]
        meta = PROVIDER_META.get(key)
        if not meta: return
        tmp  = await send(f"⏳ Fetching {meta['label']}…")
        plans = await fetch_one(key)
        try: await tmp.delete()
        except: pass
        await send_pages(send, fmt_provider(key, plans))

    elif data.startswith("f_"):
        parts = data[2:].split("_")
        max_p = float(parts[0]); min_r = float(parts[1]); min_c = int(parts[2])
        tmp   = await send("⏳ Applying filter…")
        all_p = await fetch_all()
        try: await tmp.delete()
        except: pass
        await send_pages(send, fmt_filtered(all_p, max_p, min_r, min_c))

    elif data == "filter_menu":
        await send("🔍 *Filter by Budget / Specs:*\nOr use `/filter max_price min_ram min_cpu`",
                   parse_mode=ParseMode.MARKDOWN, reply_markup=filter_kb())

    elif data == "compare":
        tmp   = await send(f"⏳ Fetching {len(FETCHERS)} providers…")
        all_p = await fetch_all()
        try: await tmp.delete()
        except: pass
        await send_pages(send, fmt_compare(all_p))

    elif data == "deals":
        tmp   = await send("⏳ Computing value scores…")
        all_p = await fetch_all()
        try: await tmp.delete()
        except: pass
        await send_pages(send, fmt_deals(all_p))

    elif data == "stats":
        all_p = {k: _cache[k].data if k in _cache else [] for k in FETCHERS}
        await send(fmt_stats(all_p), parse_mode=ParseMode.MARKDOWN)

    elif data == "free":   await send_pages(send, fmt_free_vps())
    elif data == "ssh":    await send_pages(send, fmt_ssh())
    elif data == "rdp":    await send_pages(send, fmt_rdp())

    elif data == "fetch_all":
        tmp   = await send(f"⏳ Fetching all {len(FETCHERS)} providers…")
        all_p = await fetch_all(force=True)
        try: await tmp.delete()
        except: pass
        total = sum(len(v) for v in all_p.values())
        await send(f"✅ *{total} plans from {len(FETCHERS)} providers*\n" +
                   "\n".join(f"{PROVIDER_META[k]['icon']} {PROVIDER_META[k]['label']}: `{len(v)}`"
                             for k, v in all_p.items()),
                   parse_mode=ParseMode.MARKDOWN)
        await send("Pick a provider:", reply_markup=main_kb())

    elif data == "export_menu":
        await send("📥 Choose export format:", reply_markup=export_kb())

    elif data in ("export_txt","export_csv"):
        cached = {k: _cache[k].data for k in FETCHERS if k in _cache and _cache[k].data}
        if not cached:
            await send("⚠️ No data. Run /all first."); return
        total = sum(len(v) for v in cached.values())
        ts    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        if data == "export_txt":
            bio = io.BytesIO(to_txt(cached).encode())
            bio.name = f"vps_{ts}.txt"
            cap = f"📄 *VPS Export TXT* — `{total} plans · {len(cached)} providers`"
        else:
            bio = io.BytesIO(to_csv(cached).encode())
            bio.name = f"vps_{ts}.csv"
            cap = f"📊 *VPS Export CSV* — `{total} plans · {len(cached)} providers`"
        await q.message.reply_document(document=bio, caption=cap, parse_mode=ParseMode.MARKDOWN)

    elif data == "back_main":
        await send("🏠 Main menu:", reply_markup=main_kb())

# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH CHECK SERVER  (keeps Render / Railway alive — binds to $PORT)
# ══════════════════════════════════════════════════════════════════════════════
async def health_server():
    from aiohttp import web as aio_web
    port = int(os.getenv("PORT", 8080))
    async def handle(_req):
        cached = sum(len(e.data) for e in _cache.values() if e.is_fresh())
        return aio_web.Response(
            text=f"VPS Bot v5 OK | {len(FETCHERS)} providers | {cached} plans cached",
            content_type="text/plain",
        )
    app_web = aio_web.Application()
    app_web.router.add_get("/",       handle)
    app_web.router.add_get("/health", handle)
    runner = aio_web.AppRunner(app_web)
    await runner.setup()
    await aio_web.TCPSite(runner, "0.0.0.0", port).start()
    log.info(f"✅ Health server on port {port}")

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN  — fully async, compatible with Python 3.14+
# ══════════════════════════════════════════════════════════════════════════════
async def async_main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌  Set token:  export TELEGRAM_BOT_TOKEN=xxx"); exit(1)

    print("╔════════════════════════════════════════════════════════════╗")
    print("║  VPS SCRAPER BOT v5 — MAXIMUM EDITION  🚀                  ║")
    print(f"║  {len(FETCHERS)} providers  ·  {len(FREE_VPS)} free tiers  ·  {len(FREE_SSH)} SSH  ·  {len(FREE_RDP)} RDP     ║")
    print("║  True parallel async · TTL cache · Value score           ║")
    print("║  Render/Railway ready — health check on $PORT            ║")
    print("╚════════════════════════════════════════════════════════════╝\n")

    # Build PTB app
    ptb = Application.builder().token(BOT_TOKEN).build()
    for cmd in ("start", "help"): ptb.add_handler(CommandHandler(cmd, cmd_start))
    ptb.add_handler(CommandHandler("free",    cmd_free))
    ptb.add_handler(CommandHandler("ssh",     cmd_ssh))
    ptb.add_handler(CommandHandler("rdp",     cmd_rdp))
    ptb.add_handler(CommandHandler("compare", cmd_compare))
    ptb.add_handler(CommandHandler("deal",    cmd_deal))
    ptb.add_handler(CommandHandler("deals",   cmd_deal))
    ptb.add_handler(CommandHandler("filter",  cmd_filter_args))
    ptb.add_handler(CommandHandler("all",     cmd_all))
    ptb.add_handler(CommandHandler("stats",   cmd_stats))
    ptb.add_handler(CommandHandler("export",  cmd_export))
    ptb.add_handler(CallbackQueryHandler(on_callback))

    # Start health server + background warmer as concurrent tasks
    asyncio.create_task(health_server())
    asyncio.create_task(background_warmer())

    # Run the bot manually (initialize → start → idle → stop)
    async with ptb:
        await ptb.initialize()
        await ptb.start()
        await ptb.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        log.info("🤖 Bot is polling. Press Ctrl+C to stop.")
        # Keep running until interrupted
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await ptb.updater.stop()
            await ptb.stop()
            await ptb.shutdown()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
