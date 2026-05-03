from flask import Flask, render_template, Response, request, jsonify, redirect, url_for
import cv2
import os
import sqlite3
from datetime import datetime
import match as face_match
import time
import base64
import re
import json
import threading
import csv
import io
from flask import make_response
from flask import session
from functools import wraps

app = Flask(__name__)
app.secret_key = 'faceauth_secret_2024'

# Admin credentials — change these!
ADMIN_USERNAME = 'Admin'
ADMIN_PASSWORD = 'Admin123'
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ── DATABASE ──────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect('logs.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS auth_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        mr_id TEXT,
        region TEXT,
        blink_detected INTEGER,
        timestamp TEXT,
        status TEXT,
        latitude TEXT,
        longitude TEXT,
        location_address TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS meter_readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mr_name TEXT,
        mr_id TEXT,
        consumer_id TEXT,
        consumer_name TEXT,
        previous_reading REAL,
        current_reading REAL,
        units_consumed REAL,
        reading_photo TEXT,
        timestamp TEXT,
        location TEXT,
        auth_log_id INTEGER
    )''')
    conn.commit()
    conn.close()

def log_auth(name, blink_detected):
    conn = sqlite3.connect('logs.db')
    c = conn.cursor()

    mr_id = "N/A"
    region = "N/A"
    if os.path.exists('mr_data.json'):
        with open('mr_data.json', 'r') as f:
            mr_data = json.load(f)
        if name in mr_data:
            mr_id = mr_data[name]['mr_id']
            region = mr_data[name]['region']

    status = "SUCCESS" if (name not in ["Unknown", "No face detected", "Multiple faces detected", "Not trained yet"] and blink_detected) else "FAILED"
    c.execute("""INSERT INTO auth_logs
        (name, mr_id, region, blink_detected, timestamp, status, latitude, longitude, location_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, mr_id, region, int(blink_detected),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         status, "N/A", "N/A", "N/A"))
    conn.commit()
    conn.close()
    return status

# ── GLOBAL STATE ──────────────────────────────────────────────
camera = cv2.VideoCapture(0)

current_result = {
    "name": "Scanning...",
    "blink": False,
    "blink_count": 0,
    "status": "",
    "authenticated": False
}

authenticated_name = ""
last_log_time = 0
LOG_COOLDOWN = 10

def reset_auth_state():
    global authenticated_name, last_log_time
    authenticated_name = ""
    last_log_time = 0
    current_result["name"] = "Scanning..."
    current_result["blink"] = False
    current_result["blink_count"] = 0
    current_result["status"] = ""
    current_result["authenticated"] = False
    print("✅ Auth state reset")

# ── BACKGROUND RECOGNITION THREAD ────────────────────────────
latest_frame = None
frame_lock = threading.Lock()

def recognition_loop():
    global latest_frame, authenticated_name, last_log_time
    blink_counter = 0

    while True:
        # Stop processing once authenticated
        if current_result.get("authenticated"):
            time.sleep(0.1)
            continue

        with frame_lock:
            if latest_frame is None:
                time.sleep(0.01)
                continue
            frame = latest_frame.copy()

        # Run recognition
        name, blink, ear = face_match.match_frame(frame)
        current_result["name"] = name
        current_result["blink"] = blink

        is_known = name not in [
            "Unknown", "No face detected",
            "Multiple faces detected", "Not trained yet", "Scanning..."
        ]

        # Simple blink counter — same as original code
        if is_known:
            if not blink:
                # Eyes open
                if blink_counter > 0:
                    # Was blinking before, now open = complete cycle!
                    now = time.time()
                    if (now - last_log_time) > LOG_COOLDOWN:
                        status = log_auth(name, True)
                        current_result["status"] = status
                        last_log_time = now
                        if status == "SUCCESS":
                            authenticated_name = name
                            current_result["authenticated"] = True
                            print(f"🎉 Authenticated: {name}")
                    blink_counter = 0
                else:
                    blink_counter = 0
            else:
                # Eyes closed/blinking
                blink_counter += 1
        else:
            blink_counter = 0

        time.sleep(0.03)

# Start background thread
recognition_thread = threading.Thread(target=recognition_loop, daemon=True)
recognition_thread.start()

# ── CAMERA STREAM ─────────────────────────────────────────────
def generate_frames():
    global latest_frame

    while True:
        success, frame = camera.read()
        if not success:
            break

        # Store frame for recognition thread
        with frame_lock:
            latest_frame = frame.copy()

        # Draw results on frame
        name = current_result["name"]
        blink = current_result["blink"]
        status = current_result["status"]

        is_known = name not in [
            "Unknown", "No face detected",
            "Multiple faces detected", "Not trained yet", "Scanning..."
        ]
        color = (0, 255, 0) if is_known else (0, 0, 255)

        cv2.putText(frame, f"Name: {name}", (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
        cv2.putText(frame, f"Blink: {'Yes' if blink else 'No'}", (20, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)

        if status == "SUCCESS":
            cv2.putText(frame, "AUTHENTICATED!", (20, 150),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        elif status == "FAILED":
            cv2.putText(frame, "AUTH FAILED", (20, 150),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        ret, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

# ── ROUTES ────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    next_page = request.args.get('next', 'dashboard')
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        next_page = request.form.get('next', 'dashboard')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for(next_page))
        else:
            error = 'Invalid username or password'
    return render_template('login.html', error=error, next_page=next_page)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/authenticate')
def authenticate():
    reset_auth_state()
    time.sleep(0.3)
    return render_template('authenticate.html')

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/result')
def result():
    return jsonify(current_result)

@app.route('/success')
def success():
    return render_template('success.html',
                           name=authenticated_name.replace('_', ' ').title())

@app.route('/log_location', methods=['POST'])
def log_location():
    try:
        data = request.get_json()
        latitude = data.get('latitude', 'N/A')
        longitude = data.get('longitude', 'N/A')
        address = data.get('address', 'N/A')
        conn = sqlite3.connect('logs.db')
        c = conn.cursor()
        c.execute("""UPDATE auth_logs
            SET latitude=?, longitude=?, location_address=?
            WHERE id=(SELECT MAX(id) FROM auth_logs WHERE status='SUCCESS')""",
            (str(latitude), str(longitude), address))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/enroll', methods=['GET', 'POST'])
@login_required
def enroll():
    if request.method == 'POST':
        name = request.form.get('name').strip().replace(" ", "_")
        files = request.files.getlist('photos')
        person_dir = os.path.join('dataset', name)
        os.makedirs(person_dir, exist_ok=True)
        existing = len(os.listdir(person_dir))
        for i, file in enumerate(files):
            file.save(os.path.join(person_dir, f"{existing + i}.jpg"))
        face_match.train()
        return redirect(url_for('authenticate'))
    return render_template('enroll.html')

@app.route('/enroll_webcam', methods=['POST'])
@login_required
def enroll_webcam():
    try:
        data = request.get_json()
        name = data['name'].strip().replace(" ", "_")
        mr_id = data.get('mr_id', '').strip()
        region = data.get('region', '').strip()
        photos = data['photos']

        person_dir = os.path.join('dataset', name)
        os.makedirs(person_dir, exist_ok=True)
        existing = len(os.listdir(person_dir))

        for i, photo_data in enumerate(photos):
            img_data = re.sub('^data:image/.+;base64,', '', photo_data)
            img_bytes = base64.b64decode(img_data)
            with open(os.path.join(person_dir, f"{existing + i}.jpg"), 'wb') as f:
                f.write(img_bytes)

        # Save MR info
        mr_data = {}
        if os.path.exists('mr_data.json'):
            with open('mr_data.json', 'r') as f:
                mr_data = json.load(f)
        mr_data[name] = {
            'mr_id': mr_id,
            'region': region,
            'full_name': data['name'].strip(),
            'enrolled_on': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        with open('mr_data.json', 'w') as f:
            json.dump(mr_data, f, indent=2)

        face_match.train()
        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect('logs.db')
    c = conn.cursor()
    c.execute("SELECT * FROM auth_logs ORDER BY id DESC LIMIT 100")
    logs = c.fetchall()
    conn.close()

    # Count stats manually to avoid Jinja filter issues with tuples
    total = len(logs)
    success_count = sum(1 for log in logs if log[6] == 'SUCCESS')
    failed_count = sum(1 for log in logs if log[6] == 'FAILED')
    success_rate = round((success_count / total * 100)) if total > 0 else 0

    return render_template('dashboard.html', 
                           logs=logs,
                           total=total,
                           success_count=success_count,
                           failed_count=failed_count,
                           success_rate=success_rate)

@app.route('/export_csv')
def export_csv():
    conn = sqlite3.connect('logs.db')
    c = conn.cursor()
    c.execute("SELECT * FROM auth_logs ORDER BY id DESC")
    logs = c.fetchall()
    conn.close()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow(['ID', 'Name', 'MR ID', 'Region', 'Blink Detected', 
                     'Timestamp', 'Status', 'Latitude', 'Longitude', 'Location'])

    # Data rows
    for log in logs:
        writer.writerow([
            log[0],
            log[1].replace('_', ' ') if log[1] else '',
            log[2], log[3],
            'Yes' if log[4] else 'No',
            log[5], log[6], log[7], log[8], log[9]
        ])

    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename=auth_logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    response.headers['Content-Type'] = 'text/csv'
    return response

@app.route('/release_camera')
def release_camera():
    global camera
    try:
        camera.release()
    except:
        pass
    return jsonify({'success': True})

@app.route('/reacquire_camera')
def reacquire_camera():
    global camera
    try:
        camera = cv2.VideoCapture(0)
    except:
        pass
    return jsonify({'success': True})

@app.route('/meter_reading', methods=['GET', 'POST'])
def meter_reading():
    # Only accessible after successful authentication
    if not current_result.get('authenticated'):
        return redirect(url_for('authenticate'))

    if request.method == 'POST':
        try:
            consumer_id = request.form.get('consumer_id', '').strip()
            consumer_name = request.form.get('consumer_name', '').strip()
            previous_reading = float(request.form.get('previous_reading', 0))
            current_reading = float(request.form.get('current_reading', 0))
            units_consumed = current_reading - previous_reading
            location = request.form.get('location', 'N/A')

            # Handle meter photo upload
            reading_photo = None
            if 'meter_photo' in request.files:
                photo = request.files['meter_photo']
                if photo.filename:
                    photo_dir = os.path.join('static', 'meter_photos')
                    os.makedirs(photo_dir, exist_ok=True)
                    filename = f"{consumer_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                    photo.save(os.path.join(photo_dir, filename))
                    reading_photo = filename

            # Save to database
            conn = sqlite3.connect('logs.db')
            c = conn.cursor()

            # Get MR info
            mr_name = authenticated_name
            mr_id = "N/A"
            if os.path.exists('mr_data.json'):
                with open('mr_data.json', 'r') as f:
                    mr_data = json.load(f)
                if mr_name in mr_data:
                    mr_id = mr_data[mr_name]['mr_id']

            c.execute("""INSERT INTO meter_readings
                (mr_name, mr_id, consumer_id, consumer_name, previous_reading,
                 current_reading, units_consumed, reading_photo, timestamp, location)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mr_name, mr_id, consumer_id, consumer_name,
                 previous_reading, current_reading, units_consumed,
                 reading_photo, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 location))
            conn.commit()
            conn.close()

            return redirect(url_for('reading_success'))

        except Exception as e:
            return render_template('meter_reading.html',
                                   name=authenticated_name.replace('_', ' ').title(),
                                   error=str(e))

    return render_template('meter_reading.html',
                           name=authenticated_name.replace('_', ' ').title())

@app.route('/reading_success')
def reading_success():
    return render_template('reading_success.html',
                           name=authenticated_name.replace('_', ' ').title())

@app.route('/readings_dashboard')
@login_required
def readings_dashboard():
    conn = sqlite3.connect('logs.db')
    c = conn.cursor()
    c.execute("SELECT * FROM meter_readings ORDER BY id DESC")
    readings = c.fetchall()
    conn.close()
    return render_template('readings_dashboard.html', readings=readings)

@app.route('/export_readings_csv')
@login_required
def export_readings_csv():
    conn = sqlite3.connect('logs.db')
    c = conn.cursor()
    c.execute("SELECT * FROM meter_readings ORDER BY id DESC")
    readings = c.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'MR Name', 'MR ID', 'Consumer ID', 'Consumer Name',
                     'Previous Reading', 'Current Reading', 'Units Consumed',
                     'Photo', 'Timestamp', 'Location'])
    for r in readings:
        writer.writerow(r)

    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename=meter_readings_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    response.headers['Content-Type'] = 'text/csv'
    return response


if __name__ == '__main__':
    init_db()
    app.run(debug=True)