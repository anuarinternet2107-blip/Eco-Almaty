from flask import Flask, render_template, request, jsonify, send_from_directory
from google import genai
import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv
import PIL.Image
import io

load_dotenv()

client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            org_name TEXT,
            created_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL,
            lng REAL,
            photo_path TEXT,
            trash_type TEXT,
            severity TEXT,
            status TEXT DEFAULT 'open',
            claimed_by TEXT,
            after_photo_path TEXT,
            comment TEXT,
            created_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS organisations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            passcode TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

# ─── AUTH ──────────────────────────────────────────────

@app.route('/api/check-username')
def check_username():
    username = request.args.get('username', '').strip()
    if not username:
        return jsonify({'taken': False})
    conn = get_db()
    exists = conn.execute(
        "SELECT id FROM users WHERE LOWER(username)=LOWER(?)", (username,)
    ).fetchone()
    conn.close()
    return jsonify({'taken': bool(exists)})

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or len(username) < 3:
        return jsonify({'error': 'short_username'}), 400
    if not password or len(password) < 8:
        return jsonify({'error': 'short_password'}), 400

    conn = get_db()
    exists = conn.execute(
        "SELECT id FROM users WHERE LOWER(username)=LOWER(?)", (username,)
    ).fetchone()
    if exists:
        conn.close()
        return jsonify({'error': 'taken'}), 400

    conn.execute(
        "INSERT INTO users (username, password, created_at) VALUES (?, ?, ?)",
        (username, password, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE LOWER(username)=LOWER(?) AND password=?",
        (username, password)
    ).fetchone()
    conn.close()

    if not user:
        return jsonify({'error': 'invalid'}), 401
    return jsonify({'success': True, 'username': user['username']})

# ─── PAGES ─────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/map')
def map_page():
    role = request.args.get('role', 'reporter')
    username = request.args.get('username', '')
    return render_template('map.html', role=role, username=username)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ─── REPORTS ───────────────────────────────────────────

@app.route('/api/reports', methods=['GET'])
def get_reports():
    conn = get_db()
    reports = conn.execute(
        "SELECT * FROM reports WHERE status != 'cleaned'"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in reports])

@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    uncleaned = conn.execute(
        "SELECT COUNT(*) as count FROM reports WHERE status != 'cleaned'"
    ).fetchone()['count']
    conn.close()
    return jsonify({'uncleaned': uncleaned})

# ─── ORGANISATIONS ─────────────────────────────────────

@app.route('/api/org/create', methods=['POST'])
def create_org():
    data = request.get_json()
    name = data.get('name', '').strip()
    passcode = data.get('passcode', '').strip()
    username = data.get('username', '').strip()

    if not name or not passcode or not username:
        return jsonify({'error': 'missing'}), 400
    if len(passcode) < 8:
        return jsonify({'error': 'short'}), 400

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM organisations WHERE name=?", (name,)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({'error': 'taken'}), 400

    conn.execute(
        "INSERT INTO organisations (name, passcode, created_by, created_at) VALUES (?, ?, ?, ?)",
        (name, passcode, username, datetime.now().isoformat())
    )
    conn.execute(
        "UPDATE users SET org_name=? WHERE LOWER(username)=LOWER(?)",
        (name, username)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'name': name, 'is_admin': True})

@app.route('/api/org/join', methods=['POST'])
def join_org():
    data = request.get_json()
    name = data.get('name', '').strip()
    passcode = data.get('passcode', '').strip()
    username = data.get('username', '').strip()

    conn = get_db()
    org = conn.execute(
        "SELECT * FROM organisations WHERE name=? AND passcode=?", (name, passcode)
    ).fetchone()

    if not org:
        conn.close()
        return jsonify({'error': 'not_found'}), 404

    conn.execute(
        "UPDATE users SET org_name=? WHERE LOWER(username)=LOWER(?)",
        (name, username)
    )
    conn.commit()

    is_admin = (org['created_by'].lower() == username.lower())
    conn.close()
    return jsonify({'success': True, 'name': org['name'], 'is_admin': is_admin})

@app.route('/api/org/leave', methods=['POST'])
def leave_org():
    data = request.get_json()
    username = data.get('username', '').strip()
    if not username:
        return jsonify({'error': 'missing'}), 400

    conn = get_db()
    conn.execute(
        "UPDATE users SET org_name=NULL WHERE LOWER(username)=LOWER(?)", (username,)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/org/members', methods=['GET'])
def org_members():
    org_name = request.args.get('org', '').strip()
    requester = request.args.get('username', '').strip()
    if not org_name:
        return jsonify({'error': 'missing'}), 400

    conn = get_db()
    org = conn.execute(
        "SELECT created_by FROM organisations WHERE name=?", (org_name,)
    ).fetchone()
    members = conn.execute(
        "SELECT username FROM users WHERE org_name=?", (org_name,)
    ).fetchall()
    conn.close()

    if not org:
        return jsonify({'error': 'not_found'}), 404

    is_admin = (org['created_by'].lower() == requester.lower())
    return jsonify({
        'members': [m['username'] for m in members],
        'admin': org['created_by'],
        'is_admin': is_admin
    })

@app.route('/api/org/kick', methods=['POST'])
def kick_member():
    data = request.get_json()
    requester = data.get('requester', '').strip()
    target = data.get('target', '').strip()
    org_name = data.get('org_name', '').strip()

    if not requester or not target or not org_name:
        return jsonify({'error': 'missing'}), 400

    conn = get_db()
    org = conn.execute(
        "SELECT created_by FROM organisations WHERE name=?", (org_name,)
    ).fetchone()

    if not org or org['created_by'].lower() != requester.lower():
        conn.close()
        return jsonify({'error': 'unauthorized'}), 403

    conn.execute(
        "UPDATE users SET org_name=NULL WHERE LOWER(username)=LOWER(?)", (target,)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ─── CLAIM / CLEAN ─────────────────────────────────────

@app.route('/api/report', methods=['POST'])
def submit_report():
    file = request.files.get('photo')
    lat = request.form.get('lat')
    lng = request.form.get('lng')
    comment = request.form.get('comment', '').strip()

    if not file or not lat or not lng or not comment:
        return jsonify({'error': 'Missing data'}), 400

    image_data = file.read()

    try:
        image = PIL.Image.open(io.BytesIO(image_data))
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image, """Analyze this image. Reply in this exact format and nothing else:
IS_TRASH: yes or no
TYPE: (only if yes) plastic_bags / bottles / construction / organic / cardboard / electronics / clothing / mixed / other
SEVERITY: (only if yes) light / medium / severe"""]
        )

        result = response.text
        lines = result.strip().split('\n')
        parsed = {}
        for line in lines:
            if ':' in line:
                key, val = line.split(':', 1)
                parsed[key.strip()] = val.strip().lower()

        if parsed.get('IS_TRASH', '') != 'yes':
            return jsonify({'error': 'not_trash'}), 400

        comment_check = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[f"Does this comment contain swear words, inappropriate content, or offensive language? Reply only: IS_OK: yes or no\n\nComment: {comment}"]
        )
        if 'yes' not in comment_check.text.lower():
            return jsonify({'error': 'bad_comment'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    filename = f"{datetime.now().timestamp()}.jpg"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    with open(filepath, 'wb') as f:
        f.write(image_data)

    conn = get_db()
    import random
    lat = float(lat) + random.uniform(-0.001, 0.001)
    lng = float(lng) + random.uniform(-0.001, 0.001)
    conn.execute(
        "INSERT INTO reports (lat, lng, photo_path, trash_type, severity, comment, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (lat, lng, filename, parsed.get('TYPE', 'unknown'), parsed.get('SEVERITY', 'medium'), comment, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'type': parsed.get('TYPE'), 'severity': parsed.get('SEVERITY')})

@app.route('/api/claim/<int:report_id>', methods=['POST'])
def claim_report(report_id):
    data = request.get_json()
    org_name = data.get('org_name', 'Неизвестно') if data else 'Неизвестно'
    conn = get_db()
    conn.execute(
        "UPDATE reports SET status='claimed', claimed_by=? WHERE id=?",
        (org_name, report_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/clean/<int:report_id>', methods=['POST'])
def clean_report(report_id):
    file = request.files.get('photo')
    if not file:
        return jsonify({'error': 'Missing photo'}), 400

    image_data = file.read()

    try:
        image = PIL.Image.open(io.BytesIO(image_data))
        conn2 = get_db()
        report = conn2.execute("SELECT photo_path FROM reports WHERE id=?", (report_id,)).fetchone()
        conn2.close()

        before_image = PIL.Image.open(os.path.join(UPLOAD_FOLDER, report['photo_path']))
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                before_image,
                image,
                "The first image shows a location with trash. The second image is supposed to show the same location after cleaning. Has the trash been removed? Reply with only: IS_CLEAN: yes or no"
            ]
        )

        is_clean = 'yes' in response.text.lower()
        if not is_clean:
            return jsonify({'error': 'not_clean'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500

    filename = f"clean_{datetime.now().timestamp()}.jpg"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    with open(filepath, 'wb') as f:
        f.write(image_data)

    conn = get_db()
    conn.execute(
        "UPDATE reports SET status='cleaned', after_photo_path=? WHERE id=?",
        (filename, report_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0')