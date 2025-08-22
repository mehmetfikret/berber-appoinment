from flask import Flask, render_template, request, redirect, session
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from datetime import timedelta
from dotenv import load_dotenv
import os
port = int(os.environ.get("PORT", 10000))
load_dotenv()

PAZAR_KAPALI = True

# Veritabanı dosyasını kalıcı depolama alanına yönlendirme
def get_db():
    db_path = os.path.join(os.path.dirname(__file__), 'db.sqlite3')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# Veritabanı tablolarını oluşturma
conn = get_db()
c = conn.cursor()
c.execute('CREATE TABLE IF NOT EXISTS Users (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE, is_admin BOOLEAN)')
c.execute('CREATE TABLE IF NOT EXISTS Appointments (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, service TEXT, date TEXT, time TEXT, status TEXT)')
conn.commit()
conn.close()


app = Flask(__name__)
app.secret_key = 'secret_key'

# ADMIN_PHONE = 'berber123'
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


def send_email_notification(service, date, time, phone):
    sender = os.getenv("EMAIL_ADDRESS")
    receiver = os.getenv("EMAIL_RECEIVER")
    password = os.getenv("EMAIL_PASSWORD")
    subject = "📅 Yeni Randevu Talebi"

    # E-posta içeriği
    body = f"""
Merhaba,

Yeni bir randevu talebi alındı:

📱 Kullanıcı Telefonu: {phone}
💈 Hizmet: {service}
📅 Tarih: {date}
⏰ Saat: {time}

Randevuları admin panelinden yönetebilirsiniz.
"""

    # Mail nesnesi oluştur
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = receiver
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    try:
        # SMTP sunucusuna bağlan ve mail gönder
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print("✅ Randevu bildirimi e-posta ile gönderildi.")
    except Exception as e:
        print("❌ E-posta gönderilemedi:", e)



def get_available_slots(date):
    # Tüm mevcut saatleri oluştur (08:00 - 18:00 arası 30 dakika aralıklarla)
    slots = []
    start_time = datetime.strptime("09:00", "%H:%M")
    for i in range(22):  # 20 slot = 10 saat
        slot_time = (start_time + timedelta(minutes=30*i)).strftime("%H:%M")
        slots.append(slot_time)

    # Veritabanından dolu saatleri al
    conn = get_db()
    c = conn.cursor()
    # SADECE onaylanmış randevuları dolu olarak kabul et
    c.execute('SELECT time FROM Appointments WHERE date = ? AND status IN ("approved", "pending")', (date,))
    taken_raw = [row['time'] for row in c.fetchall()]
    conn.close()

    # Saatleri normalize et
    taken = []
    for t in taken_raw:
        try:
            # Hem "8:30" hem de "08:30" formatlarını "08:30" formatına dönüştür
            if ':' in t:
                time_obj = datetime.strptime(t, "%H:%M")
                formatted = time_obj.strftime("%H:%M")
                taken.append(formatted)
        except ValueError:
            # Geçersiz saat formatı varsa atla
            continue

    return slots, taken


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form['phone']
        session['phone'] = phone
        session['is_admin'] = (phone == ADMIN_PASSWORD)
        conn = get_db()
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS Users (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE, is_admin BOOLEAN)')
        c.execute('INSERT OR IGNORE INTO Users (phone, is_admin) VALUES (?, ?)', (phone, False))
        conn.commit()
        conn.close()
        return redirect('/admin' if session['is_admin'] else '/dashboard')
    return render_template('login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def user_dashboard():
    if 'phone' not in session or session.get('is_admin'):
        return redirect('/')

    today = datetime.today().strftime('%Y-%m-%d')
    selected_date = request.args.get('date') or today
    error_message = None

    conn = get_db()
    c = conn.cursor()

    # Kullanıcının ID'sini al
    c.execute('SELECT id FROM Users WHERE phone = ?', (session['phone'],))
    user_row = c.fetchone()
    if not user_row:
        conn.close()
        return "Kullanıcı bulunamadı", 400

    user_id = user_row['id']

    if request.method == 'POST':
        service = request.form['service']
        date = request.form['date']
        time = request.form['time']

        # Pazar kontrolü
        if PAZAR_KAPALI:
            selected_day = datetime.strptime(date, "%Y-%m-%d").weekday()
            if selected_day == 6:
                error_message = "⚠️ Pazar günleri randevu alınamaz. Lütfen başka bir gün seçin."

        # Saat kontrolü
        if not error_message and (time < "09:00" or time > "20:00"):
            error_message = "⏰ Geçersiz saat. Lütfen 08:00 - 18:00 arasında bir saat seçin."
        
        # Mevcut randevu var mı kontrolü (sadece onaylanmış ve bekleyen randevular)
        if not error_message:
            c.execute('SELECT id FROM Appointments WHERE date = ? AND time = ? AND status IN ("approved", "pending")', (date, time))
            existing = c.fetchone()
            if existing:
                error_message = f"⚠️ {date} - {time} saatinde zaten bir randevu mevcut."

        if error_message:
            c.execute('SELECT id, service, date, time, status FROM Appointments WHERE user_id = ?', (user_id,))
            appointments = c.fetchall()
            all_slots, taken_slots = get_available_slots(selected_date)
            conn.close()
            return render_template('user_dashboard.html',
                                   appointments=appointments,
                                   today=today,
                                   selected_date=selected_date,
                                   error_message=error_message,
                                   pazar_kapali=PAZAR_KAPALI,
                                   slots=all_slots,
                                   taken=taken_slots)

        # Kayıt işlemi
        c.execute('INSERT INTO Appointments (user_id, service, date, time, status) VALUES (?, ?, ?, ?, ?)',
                  (user_id, service, date, time, 'pending'))
        conn.commit()
        conn.close()
        send_email_notification(service, date, time, session['phone'])
        return redirect('/dashboard')

    # GET işlemi
    c.execute('SELECT id, service, date, time, status FROM Appointments WHERE user_id = ?', (user_id,))
    appointments = c.fetchall()
    all_slots, taken_slots = get_available_slots(selected_date)
    conn.close()

    return render_template('user_dashboard.html',
                           appointments=appointments,
                           today=today,
                           selected_date=selected_date,
                           error_message=error_message,
                           pazar_kapali=PAZAR_KAPALI,
                           slots=all_slots,
                           taken=taken_slots)

@app.route('/admin')
def admin_dashboard():
    if 'phone' not in session or not session.get('is_admin'):
        return redirect('/')

    selected_date = request.args.get('date')
    today = datetime.today().strftime('%Y-%m-%d')
    tomorrow = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')

    conn = get_db()
    c = conn.cursor()

    if selected_date == "all" or not selected_date:
        c.execute('''SELECT Appointments.id, Users.phone, service, date, time, status 
                     FROM Appointments JOIN Users ON Appointments.user_id = Users.id''')
    else:
        c.execute('''SELECT Appointments.id, Users.phone, service, date, time, status 
                     FROM Appointments JOIN Users ON Appointments.user_id = Users.id
                     WHERE date = ?''', (selected_date,))

    all_appointments = c.fetchall()
    conn.close()

    # Randevuları ayır
    pending = [r for r in all_appointments if r['status'] == 'pending']
    approved = [r for r in all_appointments if r['status'] == 'approved']
    rejected = [r for r in all_appointments if r['status'] == 'rejected']

    return render_template('admin_dashboard.html',
                           pending=pending,
                           approved=approved,
                           rejected=rejected,
                           count_pending=len(pending),
                           count_approved=len(approved),
                           count_rejected=len(rejected),
                           selected_date=selected_date,
                           today=today,
                           tomorrow=tomorrow)

@app.route('/admin/week')
def admin_week():
    if 'phone' not in session or not session.get('is_admin'):
        return redirect('/')

    today = datetime.today().date()
    week_dates = [(today + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]

    conn = get_db()
    c = conn.cursor()

    weekly_data = {}
    for day in week_dates:
        c.execute('''SELECT Appointments.id, Users.phone, service, date, time, status 
                     FROM Appointments JOIN Users ON Appointments.user_id = Users.id
                     WHERE date = ?''', (day,))
        results = c.fetchall()
        # Saat sıralaması
        sorted_results = sorted(results, key=lambda r: datetime.strptime(r['time'], "%H:%M"))
        weekly_data[day] = sorted_results
    
    conn.close()
    return render_template('admin_week.html', week=weekly_data)

@app.route('/update/<int:id>/<status>')
def update_status(id, status):
    if 'phone' not in session or not session.get('is_admin'):
        return redirect('/')
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE Appointments SET status = ? WHERE id = ?', (status, id))
    conn.commit()
    conn.close()
    return redirect('/admin')

@app.route('/cancel/<int:id>')
def cancel_appointment(id):
    if 'phone' not in session or session.get('is_admin'):
        return redirect('/')

    conn = get_db()
    c = conn.cursor()

    # Önce giriş yapan kullanıcının ID'sini al
    c.execute('SELECT id FROM Users WHERE phone = ?', (session['phone'],))
    user = c.fetchone()
    if not user:
        conn.close()
        return "Kullanıcı bulunamadı", 400

    user_id = user['id']

    # Randevu gerçekten bu kullanıcıya mı ait? Kontrol et
    c.execute('SELECT id FROM Appointments WHERE id = ? AND user_id = ?', (id, user_id))
    result = c.fetchone()

    if result:
        # İptal işlemi
        c.execute('UPDATE Appointments SET status = ? WHERE id = ?', ('cancelled', id))
        conn.commit()

    conn.close()
    return redirect('/dashboard')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)