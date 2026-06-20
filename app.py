from flask import Flask, render_template, request, jsonify
import requests
import datetime
import concurrent.futures
import asyncio
import logging
import base64
import os

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- API endpoints ----------
PLAYER_API = "https://player-info-ob53.vercel.app/player-info"
BANNER_API = "https://banner-api-lac.vercel.app/profile"
OUTFIT_API = "https://output-api-ob53.vercel.app/outfit-image"

def safe_request(url, timeout=30):
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp
    except Exception as e:
        logger.error(f"Request error: {e}")
    return None

def ts(value):
    if not value:
        return "None"
    try:
        return datetime.datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return value

def fetch_player_info(uid):
    for attempt in range(3):
        resp = safe_request(f"{PLAYER_API}?uid={uid}&_t={int(datetime.datetime.now().timestamp())}", timeout=30)
        if resp and resp.status_code == 200:
            try:
                return resp.json()
            except:
                pass
    return None

def fetch_banner(uid):
    for _ in range(3):
        resp = safe_request(f"{BANNER_API}?uid={uid}", timeout=30)
        if resp and resp.status_code == 200 and resp.content:
            return resp.content
    return None

def fetch_outfit(uid):
    resp = safe_request(f"{OUTFIT_API}?uid={uid}&key=XEROX", timeout=90)
    if resp and resp.status_code == 200 and resp.content:
        return resp.content
    return None

async def fetch_media_parallel(uid):
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        banner_future = loop.run_in_executor(executor, fetch_banner, uid)
        outfit_future = loop.run_in_executor(executor, fetch_outfit, uid)
        banner, outfit = await asyncio.gather(banner_future, outfit_future)
    return banner, outfit

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/player')
def get_player():
    uid = request.args.get('uid', '').strip()
    if not uid:
        return jsonify({'error': 'Missing UID'}), 400

    player_data = fetch_player_info(uid)
    if not player_data:
        return jsonify({'error': f'UID {uid} not found or not registered'}), 404

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        banner_bytes, outfit_bytes = loop.run_until_complete(fetch_media_parallel(uid))
        loop.close()
    except Exception as e:
        logger.error(f"Media fetch error: {e}")
        banner_bytes = outfit_bytes = None

    banner_b64 = base64.b64encode(banner_bytes).decode('utf-8') if banner_bytes else None
    outfit_b64 = base64.b64encode(outfit_bytes).decode('utf-8') if outfit_bytes else None

    # Clean up the response data - ensure it's properly structured
    response_data = {
        'info': player_data,
        'banner_base64': banner_b64,
        'outfit_base64': outfit_b64
    }
    
    return jsonify(response_data)

if __name__ == '__main__':
    # Create templates folder if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)