import urllib.request
import urllib.parse
import json
import os
import time
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

BOT_TOKEN = "8834956870:AAGzNwBXB8zJOQZsFIp9vJpoO-GU77lRaq8"
CHAT_ID = "975408010"
STATE_FILE = "seen_pechersk_flats.json"

# --- LIGHTWEIGHT HTTP HEALTHCHECK SERVER FOR RENDER WEB SERVICE ---

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Pechersk Rent Radar is running healthy!")

    def log_message(self, format, *args):
        # Mute http access logs to keep console clean
        pass

def start_healthcheck_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Healthcheck HTTP server started on port {port}")
    server.serve_forever()

def fix_encoding(text):
    if not isinstance(text, str):
        return text
    try:
        return text.encode('latin1').decode('utf-8')
    except Exception:
        return text

# --- TELEGRAM API HELPERS ---

def tg_api(method, data=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        if data:
            payload = json.dumps(data).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        else:
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        print(f"TG API error ({method}): {e}")
        return None

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_api("sendMessage", payload)

def get_main_keyboard():
    return {
        "keyboard": [
            [{"text": "🔍 Актуальні варіанти (ТОП)"}],
            [{"text": "⚡ Перевірити ринок зараз"}]
        ],
        "resize_keyboard": True
    }

# --- OLX SCRAPER & ANALYZER ---

def fetch_pechersk_ads(max_price=15000):
    url = f'https://www.olx.ua/uk/nedvizhimost/kvartiry/dolgosrochnaya-arenda-kvartir/kiev/?search%5Bdistrict_id%5D=11&search%5Bfilter_float_price%3Ato%5D={max_price}&search%5Border%5D=created_at%3Adesc'
    html = ""
    # Try curl first
    try:
        res = subprocess.run([
            'curl', '-s', '-L', '-A',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            url
        ], capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=15)
        html = res.stdout
    except Exception:
        try:
            res = subprocess.run([
                'curl.exe', '-s', '-L', '-A',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                url
            ], capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=15)
            html = res.stdout
        except Exception:
            pass

    if not html:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.8'
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print("Fetch error:", e)
            return []

    marker = 'window.__PRERENDERED_STATE__'
    idx = html.find(marker)
    if idx == -1:
        return []
    
    try:
        eq_idx = html.find('=', idx)
        first_quote = html.find('"', eq_idx)
        end_quote = html.find('";', first_quote)
        raw = html[first_quote+1:end_quote]
        raw_clean = raw.encode('utf-8').decode('unicode_escape')
        data = json.loads(raw_clean)
        return data.get('listing', {}).get('listing', {}).get('ads', [])
    except Exception as e:
        print("Parse JSON error:", e)
        return []

def analyze_ad(ad):
    title = fix_encoding(ad.get('title', ''))
    desc = fix_encoding(ad.get('description', ''))
    price_val = ad.get('price', {}).get('regularPrice', {}).get('value', 0)
    price_str = fix_encoding(ad.get('price', {}).get('displayValue', ''))
    url = ad.get('url', '')
    user_name = fix_encoding(ad.get('user', {}).get('name', 'Власник'))
    user_created = ad.get('user', {}).get('created', '')
    
    params = {fix_encoding(p.get('name')): fix_encoding(p.get('value')) for p in ad.get('params', [])}
    repair = params.get('Ремонт', 'Не вказано')
    floor = params.get('Поверх', '?')
    total_floors = params.get('Поверховість', '?')
    area = params.get('Загальна площа', '?')
    
    full_text = (title + " " + desc).lower()
    
    bad_keywords = ['радянськ', 'сталінськ', 'жилий стан', 'радянський', 'бабушк', 'ковролин', 'без ремонту', 'під ремонт']
    is_babushka = any(k in full_text for k in bad_keywords) or repair == 'Житловий стан'
    
    tags = []
    if 'генератор' in full_text:
        tags.append('🔋 Генератор')
    if 'starlink' in full_text or 'старлінк' in full_text:
        tags.append('🛰️ Starlink')
    if 'перша здача' in full_text:
        tags.append('✨ Перша здача')
    if 'авторський' in str(repair).lower() or 'дизайнер' in full_text:
        tags.append('💎 Дизайнерський')
    elif 'євроремонт' in str(repair).lower() or 'євро' in full_text:
        tags.append('✨ Євроремонт')
    elif 'косметичний' in str(repair).lower():
        tags.append('🔹 Косметичний')
        
    if 'без комісії' in full_text or params.get('Без комісії'):
        tags.append('💰 Без комісії')
        
    photos = ad.get('photos', [])
    main_photo = photos[0] if photos else None
    
    score = 0
    if not is_babushka:
        score += 3
    if '💎 Дизайнерський' in tags or '✨ Перша здача' in tags:
        score += 4
    elif '✨ Євроремонт' in tags:
        score += 2
    if '🔋 Генератор' in tags or '🛰️ Starlink' in tags:
        score += 2
    if '💰 Без комісії' in tags:
        score += 1
    if price_val and price_val <= 14000:
        score += 2
        
    return {
        'id': ad.get('id'),
        'title': title,
        'price_val': price_val,
        'price_str': price_str,
        'url': url,
        'user_name': user_name,
        'user_created': user_created[:10] if user_created else '',
        'floor': f"{floor}/{total_floors}",
        'area': area,
        'repair': repair,
        'tags': tags,
        'is_babushka': is_babushka,
        'score': score,
        'main_photo': main_photo
    }

# --- PERSISTENCE ---

def load_seen():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_seen(seen_set):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(seen_set), f, ensure_ascii=False, indent=2)

# --- NOTIFICATIONS ---

def notify_new_flat(flat):
    msg = [
        "🔥 <b>ЗНАЙДЕНО НОВИЙ ВАРІАНТ!</b>",
        f"📍 <b>Район:</b> Печерський",
        f"💵 <b>Ціна:</b> {flat['price_str']}",
        f"🛠 <b>Ремонт:</b> {flat['repair']}",
        f"🏢 <b>Параметри:</b> {flat['area']}, поверх {flat['floor']}",
        f"👤 <b>Автор:</b> {flat['user_name']} (на OLX з {flat['user_created']})"
    ]
    if flat['tags']:
        msg.append(f"✨ <b>Фішки:</b> {', '.join(flat['tags'])}")
    if flat['is_babushka']:
        msg.append("⚠️ <i>Можливий старий фонд / бабушатник</i>")
        
    msg.append(f"\n🔗 <a href='{flat['url']}'>Відкрити оголошення на OLX</a>")
    text = "\n".join(msg)
    
    if flat['main_photo']:
        payload = {
            "chat_id": CHAT_ID,
            "photo": flat['main_photo'],
            "caption": text,
            "parse_mode": "HTML"
        }
        res = tg_api("sendPhoto", payload)
        if not res or not res.get("ok"):
            send_message(CHAT_ID, text)
    else:
        send_message(CHAT_ID, text)

def format_flats_list(flats_list, title="📋 АКТУАЛЬНІ ВАРІАНТИ (ПЕЧЕРСЬК ДО 15К)"):
    if not flats_list:
        return "Наразі немає варіантів за вашими критеріями."
        
    msg = [f"<b>{title}</b>\n"]
    for i, f in enumerate(flats_list[:7]):
        tag_str = f" [{', '.join(f['tags'])}]" if f['tags'] else ""
        babushka_warn = " ⚠️(старий ремонт)" if f['is_babushka'] else " 💎(сучасний)"
        msg.append(
            f"<b>{i+1}. {f['price_str']}</b> — {f['area']}, пов. {f['floor']}\n"
            f"🛠 Ремонт: <i>{f['repair']}</i>{babushka_warn}{tag_str}\n"
            f"🔗 <a href='{f['url']}'>{f['title'][:45]}...</a>\n"
        )
    return "\n".join(msg)

# --- BOT CONTROLLER ---

def handle_updates(offset=0):
    res = tg_api(f"getUpdates?offset={offset}&timeout=1")
    if not res or not res.get("result"):
        return offset
        
    for update in res["result"]:
        offset = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        from_id = str(msg.get("chat", {}).get("id", ""))
        
        if not text or from_id != CHAT_ID:
            continue
            
        if text in ["/start", "help", "меню"]:
            welcome = (
                "👋 <b>Привіт! Я твій особистий радар оренди на Печерську.</b>\n\n"
                "🎯 <b>Налаштування:</b>\n"
                "• Район: <b>Печерський</b>\n"
                "• Бюджет: <b>до 15 000 грн</b>\n"
                "• Фільтр: виділяємо сучасні, відсіюємо бабушатники\n\n"
                "Натискай кнопки внизу для перегляду або очікуй миттєвих пушів!"
            )
            send_message(from_id, welcome, reply_markup=get_main_keyboard())
            
        elif text == "🔍 Актуальні варіанти (ТОП)":
            send_message(from_id, "⏳ Сканую Печерськ та відбираю найсмачніші варіанти...")
            raw_ads = fetch_pechersk_ads(max_price=15000)
            analyzed = [analyze_ad(a) for a in raw_ads]
            top_clean = sorted([a for a in analyzed if not a['is_babushka']], key=lambda x: x['score'], reverse=True)
            if not top_clean:
                top_clean = sorted(analyzed, key=lambda x: x['score'], reverse=True)
            report = format_flats_list(top_clean, "🏆 ТОП СУЧАСНИХ КВАРТИР НА ПЕЧЕРСЬКУ (ДО 15К):")
            send_message(from_id, report, reply_markup=get_main_keyboard())
            
        elif text == "⚡ Перевірити ринок зараз":
            send_message(from_id, "🔎 Перевіряю свіжі надходження за останні години...")
            raw_ads = fetch_pechersk_ads(max_price=15000)
            analyzed = [analyze_ad(a) for a in raw_ads]
            report = format_flats_list(analyzed[:6], "⚡ НАЙСВІЖІШІ ОГОЛОШЕННЯ НА РИНКУ:")
            send_message(from_id, report, reply_markup=get_main_keyboard())
            
    return offset

def run_continuous_monitor():
    print("Запуск бота та моніторингу...")
    send_message(
        CHAT_ID,
        "🤖 <b>Бот онлайн на Render!</b>\nКнопки меню активні. Автоматичний моніторинг кожні 2 хвилини запущено 🚀",
        reply_markup=get_main_keyboard()
    )
    
    seen = load_seen()
    offset = 0
    last_check_time = 0
    CHECK_INTERVAL = 120  # check OLX every 2 minutes
    
    if not seen:
        init_ads = fetch_pechersk_ads(max_price=15000)
        for a in init_ads:
            seen.add(a.get('id'))
        save_seen(seen)
        print(f"Ініціалізовано {len(seen)} оголошень.")

    while True:
        try:
            offset = handle_updates(offset)
            now = time.time()
            if now - last_check_time >= CHECK_INTERVAL:
                last_check_time = now
                latest_ads = fetch_pechersk_ads(max_price=15000)
                new_flats = []
                for a in latest_ads:
                    ad_id = a.get('id')
                    if ad_id and ad_id not in seen:
                        flat = analyze_ad(a)
                        new_flats.append(flat)
                        seen.add(ad_id)
                        
                if new_flats:
                    save_seen(seen)
                    for nf in new_flats:
                        notify_new_flat(nf)
                        time.sleep(1)
                        
            time.sleep(2)
        except Exception as e:
            print("Loop error:", e)
            time.sleep(5)

if __name__ == '__main__':
    # Start background thread for HTTP healthcheck so Render Web Service marks deployment as Live
    http_thread = threading.Thread(target=start_healthcheck_server, daemon=True)
    http_thread.start()
    
    run_continuous_monitor()
