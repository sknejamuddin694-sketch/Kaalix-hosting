import os
import sys
import threading
import subprocess
import zipfile
import random
import hashlib
from datetime import datetime, timedelta
import sqlite3
from flask import Flask, request, redirect, session, url_for, render_template
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import re

# ---------------- CONFIG -------------------
PORT = 8080
DATA_DIR = "data"
BOTS_DIR = os.path.join(DATA_DIR, "bots")
DB_FILE = os.path.join(DATA_DIR, "panel.db")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BOTS_DIR, exist_ok=True)

# ---- Environment variables ----
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))  # default 0 if not set

if not BOT_TOKEN or not ADMIN_ID:
    raise ValueError("Please set BOT_TOKEN and ADMIN_ID environment variables!")

# ---------------- DATABASE ----------------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, password TEXT, verified INTEGER)")
cur.execute("CREATE TABLE IF NOT EXISTS uploads (telegram_id INTEGER, bot_name TEXT, file_size INTEGER, upload_time TEXT)")
conn.commit()

# ---------------- TELEGRAM BOT ----------------
tg = telebot.TeleBot(BOT_TOKEN)

OTP_CACHE = {}
APPROVE_CACHE = {}
RUNNING_BOTS = {}
PUBLIC_URL = None

def send_otp(tg_id):
    otp = str(random.randint(100000, 999999))
    OTP_CACHE[tg_id] = otp
    try:
        tg.send_message(
            tg_id,
            f"🛡️ 𝑲𝑨𝑨𝑳𝑰𝑿 𝑺𝑬𝑪𝑼𝑹𝑰𝑻𝒀 — Your Login OTP: `{otp}` ❗ Don't share this code.",
            parse_mode="Markdown"
        )
    except Exception as e:
        print("OTP send error:", e)

@tg.message_handler(commands=["start"])
def tg_start(msg):
    if PUBLIC_URL:
        keyboard = InlineKeyboardMarkup()
        button = InlineKeyboardButton(text="🌐 𝑶𝒑𝒆𝒏 𝑲𝑨𝑨𝑳𝑰𝑿 𝑷𝒂𝒏𝒆𝒍", url=PUBLIC_URL)
        keyboard.add(button)
        tg.send_message(
            chat_id=msg.chat.id,
            text=(
                "🚀 𝑲𝑨𝑨𝑳𝑰𝑿 𝑷𝒓𝒆𝒎𝒊𝒖𝒎 𝑷𝒂𝒏𝒆𝒍 𝑩𝒐𝒕 𝒊𝒔 𝑶𝒏𝒍𝒊𝒏𝒆\n\n"
                "👇 Click below to open the panel\n\n"
                "🔐 First-Time Login: OTP will be sent"
            ),
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        tg.reply_to(msg, "⏳ Panel is starting, please wait...")

@tg.message_handler(commands=["approve"])
def tg_approve(msg):
    APPROVE_CACHE[msg.from_user.id] = True
    tg.reply_to(msg, "✅ Access Approved! You can now use the Dashboard.")

@tg.message_handler(commands=["panel"])
def tg_panel(msg):
    if PUBLIC_URL:
        tg.reply_to(msg, f"🌐 Panel URL:\n{PUBLIC_URL}")
    else:
        tg.reply_to(msg, "⏳ Panel is starting, please wait...")

def telegram_polling():
    tg.infinity_polling()

# ---------------- FLASK APP ----------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kaalix_secret_key_123")
app.permanent_session_lifetime = timedelta(days=7)

# ---------------- ROUTES ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        try:
            tgid = int(request.form.get("tgid"))
            password = request.form.get("password")
            remember = request.form.get("remember")
            hp = hashlib.sha256(password.encode()).hexdigest()
            
            cur.execute("SELECT password, verified FROM users WHERE telegram_id=?", (tgid,))
            row = cur.fetchone()
            
            if not row:
                send_otp(tgid)
                cur.execute("INSERT INTO users VALUES (?,?,0)", (tgid, hp))
                conn.commit()
                session["pending"] = tgid
                return redirect(url_for("otp"))
            
            if row[0] == hp and row[1] == 1:
                session["user"] = tgid
                if remember: session.permanent = True
                return redirect(url_for("dashboard"))
        except: pass
    return render_template("login.html")

@app.route("/otp", methods=["GET","POST"])
def otp():
    if "pending" not in session: return redirect(url_for("login"))
    if request.method=="POST":
        code = request.form.get("otp")
        tgid = session["pending"]
        if OTP_CACHE.get(tgid) == code:
            cur.execute("UPDATE users SET verified=1 WHERE telegram_id=?", (tgid,))
            conn.commit()
            session.pop("pending")
            session["user"] = tgid
            return redirect(url_for("dashboard"))
    return render_template("otp.html")

@app.route("/dashboard")
def dashboard():
    if "user" not in session: return redirect(url_for("login"))
    uid = session["user"]
    if not APPROVE_CACHE.get(uid):
        return render_template("approval.html", base_head="{% include 'base.html' %}")
    
    user_bots = {}
    files = [f for f in os.listdir(BOTS_DIR) if f.startswith(str(uid)+"_")]
    for f in files:
        user_bots[f] = "RUNNING" if f in RUNNING_BOTS else "STOPPED"
    return render_template("dashboard.html", bots=user_bots, uid=uid)

@app.route("/upload", methods=["POST"])
def upload():
    if "user" not in session: return redirect(url_for("login"))
    uid = session["user"]
    file = request.files.get("botfile")
    if not file: return "No file"

    file.seek(0,2); size=file.tell(); file.seek(0)
    if size>1024*1024: return "File too large"

    files = [f for f in os.listdir(BOTS_DIR) if f.startswith(str(uid)+"_")]
    if len(files)>=3: return "Slot full (Max 3 bots)"

    filename = f"{uid}_{file.filename}"
    path = os.path.join(BOTS_DIR, filename)
    file.save(path)

    if filename.endswith(".zip"):
        with zipfile.ZipFile(path) as z: z.extractall(BOTS_DIR)
        os.remove(path)

    cur.execute("INSERT INTO uploads VALUES (?,?,?,?)", (uid, filename, size, datetime.now().isoformat()))
    conn.commit()
    return redirect(url_for("dashboard"))

@app.route("/startbot/<bot>")
def startbot(bot):
    path = os.path.join(BOTS_DIR, bot)
    if bot not in RUNNING_BOTS:
        p = subprocess.Popen([sys.executable, path])
        RUNNING_BOTS[bot] = p
    return redirect(url_for("dashboard"))

@app.route("/stopbot/<bot>")
def stopbot(bot):
    p = RUNNING_BOTS.get(bot)
    if p:
        try: p.terminate()
        except: pass
        RUNNING_BOTS.pop(bot, None)
    return redirect(url_for("dashboard"))

@app.route("/editbot/<bot>", methods=["GET","POST"])
def editbot(bot):
    if "user" not in session: return redirect(url_for("login"))
    path = os.path.join(BOTS_DIR, bot)
    if request.method=="POST":
        code=request.form.get("code")
        with open(path,"w",encoding="utf-8") as f: f.write(code)
        return redirect(url_for("dashboard"))

    with open(path,"r",encoding="utf-8") as f: code=f.read()
    return render_template("edit.html", botname=bot, code=code)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------- MAIN -------------------
if __name__ == "__main__":
    print("🚀 Starting KAALIX Panel...")

    threading.Thread(target=telegram_polling, daemon=True).start()

    def start_cloudflare_blocking():
        global PUBLIC_URL
        print("🌐 Starting Cloudflare Tunnel...")
        process = subprocess.Popen(
            ["cloudflared","tunnel","--url",f"http://127.0.0.1:{PORT}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )

        for line in process.stdout:
            line=line.strip()
            print(line)
            match=re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com",line)
            if match:
                PUBLIC_URL=match.group(0)
                print(f"✅ Public URL Generated: {PUBLIC_URL}")
                try:
                    keyboard=InlineKeyboardMarkup()
                    button=InlineKeyboardButton(text="🌐 Open KAALIX Panel", url=PUBLIC_URL)
                    keyboard.add(button)
                    tg.send_message(ADMIN_ID,"🚀 *KAALIX PANEL LIVE*",parse_mode="Markdown",reply_markup=keyboard)
                except Exception as e:
                    print("Telegram send error:", e)
                break

    threading.Thread(target=start_cloudflare_blocking, daemon=True).start()

    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)