from flask import Flask, render_template, request, jsonify
import requests
import datetime
import concurrent.futures
import asyncio
import logging
import base64
import json
import os
import time
from functools import wraps

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- API endpoints ----------
PLAYER_API = "https://player-info-ob53.vercel.app/player-info"
BANNER_API = "https://banner-api-lac.vercel.app/profile"
OUTFIT_API = "https://output-api-ob53.vercel.app/outfit-image"

# Free Fire Official Ban Check API
GARENA_BAN_API = "https://ff.garena.com/api/antihack/check_banned"

# Alternative API (if official one has issues)
COMMUNITY_API = "https://htgapisitedt.x10.mx/Isban.php"

# ---------- Rate Limiting ----------
# Simple rate limiter for free tier
rate_limit_store = {}
RATE_LIMIT = 5  # Max requests per IP per day
RATE_LIMIT_WINDOW = 86400  # 24 hours in seconds

def rate_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get client IP
        ip = request.remote_addr
        
        # Clean old entries
        current_time = time.time()
        if ip in rate_limit_store:
            if current_time - rate_limit_store[ip]['timestamp'] > RATE_LIMIT_WINDOW:
                # Reset if window expired
                rate_limit_store[ip] = {'count': 0, 'timestamp': current_time}
        else:
            rate_limit_store[ip] = {'count': 0, 'timestamp': current_time}
        
        # Check rate limit
        if rate_limit_store[ip]['count'] >= RATE_LIMIT:
            return jsonify({
                'error': f'Rate limit exceeded. Max {RATE_LIMIT} requests per day.',
                'reset_in': f'{int(RATE_LIMIT_WINDOW - (current_time - rate_limit_store[ip]["timestamp"]))} seconds'
            }), 429
        
        # Increment counter
        rate_limit_store[ip]['count'] += 1
        
        return f(*args, **kwargs)
    return decorated_function

# ---------- Helper Functions ----------
def safe_request(url, timeout=30, headers=None):
    try:
        if headers:
            resp = requests.get(url, timeout=timeout, headers=headers)
        else:
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

# ---------- BAN CHECK FUNCTIONS ----------
def check_ban_status_garena(uid):
    """Check ban status using Garena's official API"""
    try:
        # Garena's API expects POST with form data
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        data = {'uid': uid}
        
        resp = requests.post(GARENA_BAN_API, data=data, headers=headers, timeout=30)
        
        if resp.status_code == 200:
            result = resp.json()
            if result.get('status') == 'success':
                return {
                    'is_banned': result.get('is_banned', 0) == 1,
                    'ban_period': result.get('ban_period', 0),
                    'message': result.get('message', 'No ban info'),
                    'uid': result.get('uid', uid),
                    'nickname': result.get('nickname', 'Unknown')
                }
    except Exception as e:
        logger.error(f"Garena ban check error: {e}")
    
    return None

def check_ban_status_community(uid):
    """Check ban status using community API (fallback)"""
    try:
        resp = safe_request(f"{COMMUNITY_API}?uid={uid}", timeout=30)
        if resp and resp.status_code == 200:
            result = resp.json()
            if result.get('status') == 'success':
                return {
                    'is_banned': result.get('is_banned', 0) == 1,
                    'ban_period': result.get('ban_period', 0),
                    'message': result.get('message', 'No ban info'),
                    'uid': result.get('uid', uid),
                    'nickname': result.get('nickname', 'Unknown')
                }
    except Exception as e:
        logger.error(f"Community ban check error: {e}")
    
    return None

def check_ban_status(uid):
    """Main ban check function - tries official API first, then falls back to community API"""
    # Try Garena official API first
    result = check_ban_status_garena(uid)
    if result:
        return result
    
    # Fallback to community API
    result = check_ban_status_community(uid)
    if result:
        return result
    
    # If both fail, return unknown status
    return {
        'is_banned': None,
        'ban_period': None,
        'message': 'Unable to check ban status',
        'uid': uid,
        'nickname': 'Unknown'
    }

# ---------- Flask Routes ----------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/player')
@rate_limit
def get_player():
    uid = request.args.get('uid', '').strip()
    if not uid:
        return jsonify({'error': 'Missing UID'}), 400
    
    # Validate UID format (should be numeric)
    if not uid.isdigit():
        return jsonify({'error': 'Invalid UID format. Must be numeric.'}), 400

    # Fetch player info
    player_data = fetch_player_info(uid)
    if not player_data:
        return jsonify({'error': f'UID {uid} not found or not registered'}), 404

    # Check ban status
    ban_status = check_ban_status(uid)
    
    # Add ban info to player data
    if ban_status:
        player_data['ban_status'] = ban_status

    # Fetch media (banner and outfit)
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

    response_data = {
        'info': player_data,
        'banner_base64': banner_b64,
        'outfit_base64': outfit_b64,
        'ban_info': ban_status  # Include ban info separately for easier access
    }
    
    return jsonify(response_data)

@app.route('/api/check-ban')
@rate_limit
def check_ban_only():
    """Endpoint to check ban status only (without player info)"""
    uid = request.args.get('uid', '').strip()
    if not uid:
        return jsonify({'error': 'Missing UID'}), 400
    
    if not uid.isdigit():
        return jsonify({'error': 'Invalid UID format. Must be numeric.'}), 400
    
    ban_status = check_ban_status(uid)
    
    if not ban_status:
        return jsonify({'error': 'Unable to check ban status'}), 500
    
    return jsonify(ban_status)

@app.route('/api/status')
def api_status():
    """Check API status and rate limit info"""
    ip = request.remote_addr
    current_time = time.time()
    
    if ip in rate_limit_store:
        remaining = RATE_LIMIT - rate_limit_store[ip]['count']
        reset_time = int(RATE_LIMIT_WINDOW - (current_time - rate_limit_store[ip]['timestamp']))
    else:
        remaining = RATE_LIMIT
        reset_time = RATE_LIMIT_WINDOW
    
    return jsonify({
        'status': 'online',
        'rate_limit': {
            'max_requests': RATE_LIMIT,
            'remaining': max(0, remaining),
            'reset_in_seconds': max(0, reset_time)
        }
    })

@app.route('/api/ban-history')
def get_ban_history():
    """Get ban history from local storage (if implemented)"""
    # This would normally query a database
    # For now, return empty array
    return jsonify({
        'history': [],
        'message': 'Ban history feature coming soon'
    })

# ---------- Error Handlers ----------
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# ---------- Main ----------
if __name__ == '__main__':
    # Create templates folder if it doesn't exist
    os.makedirs('t it doesn't exist
    os.memplates', exist_akedirs('templates', exist_ok=True)
    
ok=True)
    
    # Check    # Check if index if index.html exists in templates,.html exists in templates, if not if not create it
    if not create it os.path.exists('
    if not os.path.exists('templates/index.html'):
        logger'):
        logger.warning(".warning("ttemplates/index.html notemplates/index.html not found! found! Please create it Please create it.")
    
.")
    
    #    # Run the Run the app
    app app
    app.run(host='0.0..run(host='0.0',0.0.0.0', port=5000 port=5000, debug=False,, debug=False, use_reloader=False use_reloader=False)