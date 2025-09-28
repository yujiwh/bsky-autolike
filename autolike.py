#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bluesky Auto-Like (Followers of a Source Handle) — multi-bot, single .env (prefix BOT_ID)
- Semua bot membaca daftar followers dari FOLLOWERS_SOURCE_HANDLE (shared).
- Variabel per-bot dibaca dari .env dengan prefix BOT_ID (mis. BOT1_HANDLE, BOT1_APP_PASSWORD, dst).
- BOT_ID di-inject oleh systemd template: Environment=BOT_ID=%i
"""

import os, json, time, logging
from datetime import datetime
from atproto import Client
from dotenv import load_dotenv

# === Load shared .env ===
ENV_PATH = os.getenv("ENV_PATH", "/opt/bsky-autolike/.env")
load_dotenv(ENV_PATH)

# --- Helper ambil var dengan prefix BOT_ID ---
BOT_ID = os.getenv("BOT_ID", "BOT1").strip()

def envp(key: str, default: str = "") -> str:
    # Prioritas: <BOT_ID>_KEY → KEY (fallback shared) → default
    return (os.getenv(f"{BOT_ID}_{key}") or os.getenv(key) or default).strip()

# === Shared config (tanpa prefix) ===
FOLLOWERS_SOURCE_HANDLE = envp("FOLLOWERS_SOURCE_HANDLE", "im.from.yt")
SERVICE = envp("BSKY_SERVICE", "https://bsky.social")
SHARD_TOTAL = int(envp("SHARD_TOTAL", "1"))  # total shard global (mis. 5)

# === Per-bot config (pakai prefix) ===
BOT_HANDLE = envp("HANDLE")              # contoh: BOT1_HANDLE
IDENT     = envp("IDENTIFIER") or BOT_HANDLE
PW        = envp("APP_PASSWORD")
WORKDIR   = envp("WORKDIR", f"/opt/bsky-autolike/{BOT_ID}")
SHARD_IDX = int(envp("SHARD_INDEX", "0"))

# === Tuning (shared/fallback ke per-bot kalau ada) ===
MAX_FOLLOWERS_PER_RUN = int(envp("MAX_FOLLOWERS_PER_RUN", "50"))
POSTS_PER_USER        = int(envp("POSTS_PER_USER", "5"))
SLEEP_PER_LIKE        = float(envp("SLEEP_PER_LIKE", "0.5"))

# === Paths (per-bot; wajib unik via WORKDIR) ===
os.makedirs(WORKDIR, exist_ok=True)
STATE_FILE  = f"{WORKDIR}/state.json"
DETAIL_LOG  = f"{WORKDIR}/autolike.log"
SIMPLE_LOG  = f"{WORKDIR}/autolike_simple.log"

# === Logging ===
logging.basicConfig(
    filename=DETAIL_LOG,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def log_simple(msg: str):
    try:
        with open(SIMPLE_LOG, "a") as f:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"{ts} | {msg}\n")
    except Exception as e:
        logging.error(f"Gagal tulis simple log: {e}")

# === Client helpers (kompatibel beberapa versi atproto) ===
def make_client():
    try:
        return Client(service=SERVICE)
    except TypeError:
        try: return Client(base_url=SERVICE)
        except TypeError: return Client()

def login_bot():
    if not IDENT or not PW:
        raise RuntimeError(f"[{BOT_ID}] IDENTIFIER/HANDLE atau APP_PASSWORD belum di-set.")
    c = make_client()
    c.login(IDENT, PW)
    return c

def resolve_did(c: Client, handle_or_did: str) -> str:
    if handle_or_did.startswith("did:"):
        return handle_or_did
    return c.resolve_handle(handle_or_did).did

# === State ===
def load_state():
    try:
        with open(STATE_FILE, "r") as f: return json.load(f)
    except Exception:
        return {}

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f: json.dump(state, f)
    os.replace(tmp, STATE_FILE)

# === Feed utils ===
def is_reply(post) -> bool:
    rec = getattr(post, "record", None)
    return bool(getattr(rec, "reply", None))

def rkey(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]

def iter_followers(c: Client, actor_did: str, limit_total: int):
    fetched, cursor = 0, None
    while fetched < limit_total:
        page_limit = min(100, limit_total - fetched)
        try:
            res = c.get_followers(actor=actor_did, limit=page_limit, cursor=cursor)
            followers = res.followers
            cursor = getattr(res, "cursor", None)
        except AttributeError:
            res = c.app.bsky.graph.get_followers({"actor": actor_did, "limit": page_limit, "cursor": cursor})
            followers = res.get("followers", [])
            cursor = res.get("cursor")
        for f in followers:
            did = getattr(f, "did", None) or (f.get("did") if isinstance(f, dict) else None)
            handle = getattr(f, "handle", None) or (f.get("handle") if isinstance(f, dict) else None)
            if did:
                yield did, handle
                fetched += 1
                if fetched >= limit_total: return
        if not cursor: break

def like_for_user(c: Client, user_did: str, last_seen_rkey: str):
    # ambil n post terbaru non-reply lalu like yang rkey > last_seen_rkey
    try:
        feed = c.get_author_feed(actor=user_did, limit=POSTS_PER_USER, include_pins=False)
    except TypeError:
        feed = c.get_author_feed(actor=user_did, limit=POSTS_PER_USER)
    items = []
    for item in feed.feed:
        post = item.post
        if is_reply(post): 
            continue
        items.append((rkey(post.uri), post))
    items.sort(key=lambda x: x[0])  # lama -> baru

    liked, newest = 0, last_seen_rkey or ""
    for rk, post in items:
        if last_seen_rkey and rk <= last_seen_rkey:
            continue
        try:
            make_client_like = c.like  # minor perf
            make_client_like(uri=post.uri, cid=post.cid)
            liked += 1
            newest = max(newest, rk)
            logging.info(f"[{BOT_ID}] Liked follower post: {post.uri}")
            time.sleep(SLEEP_PER_LIKE)
        except Exception as e:
            logging.error(f"[{BOT_ID}] Gagal like {post.uri}: {e}")
    return liked, newest

# === Main ===
def run_once():
    c = login_bot()
    source_did = resolve_did(c, FOLLOWERS_SOURCE_HANDLE)

    state = load_state()
    followers_state = state.get("followers", {})

    # ambil follower list (lebih banyak kalau sharding agar distribusi merata)
    raw = list(iter_followers(c, source_did, MAX_FOLLOWERS_PER_RUN * max(1, SHARD_TOTAL)))
    total = len(raw)

    processed = total_likes = scanned = 0
    for idx, (follower_did, follower_handle) in enumerate(raw):
        # sharding: ambil item yang cocok dengan shard index
        if SHARD_TOTAL > 1 and (idx % SHARD_TOTAL) != SHARD_IDX:
            continue
        scanned += 1
        last_seen = (followers_state.get(follower_did) or {}).get("last_seen_rkey", "")
        liked, newest = like_for_user(c, follower_did, last_seen)
        total_likes += liked
        processed += 1
        if newest and newest != last_seen:
            followers_state[follower_did] = {"last_seen_rkey": newest}

    state["followers"] = followers_state
    save_state(state)

    logging.info(
        f"[{BOT_ID}] Run done. source={FOLLOWERS_SOURCE_HANDLE}, total_fetch={total}, "
        f"scanned={scanned}, processed={processed}, new_likes={total_likes}, shard={SHARD_IDX}/{SHARD_TOTAL}"
    )
    log_simple(f"{BOT_ID} | Proc:{processed} Likes:{total_likes} | Shard {SHARD_IDX}/{SHARD_TOTAL}")

def main():
    for attempt in range(1, 4):
        try:
            run_once(); return
        except Exception as e:
            delay = 5 * attempt
            logging.error(f"[{BOT_ID}] Run error (attempt {attempt}): {e}")
            log_simple(f"{BOT_ID} | Run error: {e}")
            if attempt < 3: time.sleep(delay)

if __name__ == "__main__":
    main()
