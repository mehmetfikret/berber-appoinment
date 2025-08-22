from flask import Flask, render_template, request, redirect, session
import psycopg
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

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'secret_key')

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# PostgreSQL bağlantı bilgileri
DB_CONFIG = {
    'host': "dpg-d2k6qp3uibrs73ehlik0-a",
    'dbname': "berber_db",
    'user': "berber_db_user",
    'password': "7aZr61mVi3WFYy6ZZrZS7HASwHKxOVvu",
    'port': os.getenv('DB_PORT', '5432')
}

def get_db():
    """PostgreSQL bağlantısı oluştur"""
    try:
        conn = psycopg.connect(
            host=DB_CONFIG['host'],
            dbname=DB_CONFIG['dbname'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            port=DB_CONFIG['port']
        )
        return conn
    except Exception as e:
        print(f"Veritabanı bağlantı hatası: {e}")
        return None

def init_database():
    """İlk çalıştırmada tabloları oluştur"""
    conn = get_db()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        
        # Users tablosu
        cur.execute('''
            CREATE TABLE IF NOT EXISTS Users (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) UNIQUE NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE
            )
        ''')
        
        # Appointments tablosu
        cur.execute('''
            CREATE TABLE IF NOT EXISTS Appointments (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES Users(id),
                service VARCHAR(100) NOT NULL,
                date VARCHAR(10) NOT NULL,
                time VARCHAR(5) NOT NULL,
                status VARCHAR(20) DEFAULT 'pending'
            )
        ''')
        
        conn.commit()
        print("✅ Veritabanı tabloları başarıyla oluşturuldu.")
        
    except Exception as e:
        print(f"Tablo oluşturma hatası: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

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
    # Tüm mevcut saatleri oluştur (09:00 - 20:00 arası 30 dakika aralıklarla)
    slots = []
    start_time = datetime.strptime("09:00", "%H:%M")
    for i in range(22):  # 22 slot = 11 saat
        slot_time = (start_time + timedelta(minutes=30*i)).strftime("%H:%M")
        slots.append(slot_time)

    # Veritabanından dolu saatleri al
    conn = get_db()
    if not conn:
        return slots, []
    
    try:
        cur = conn.cursor()
        # SADECE onaylanmış randevuları dolu olarak kabul et
        cur.execute('SELECT time FROM Appointments WHERE date = %s AND status IN (%s, %s)', 
                   (date, 'approved', 'pending'))
        taken_raw = [row[0] for row in cur.fetchall()]
        
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
        
    except Exception as e:
        print(f"Slot sorgulama hatası: {e}")
        return slots, []
    finally:
        cur.close()
        conn.close()

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form['phone']
        session['phone'] = phone
        session['is_admin'] = (phone == ADMIN_PASSWORD)
        
        conn = get_db()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute('INSERT INTO Users (phone, is_admin) VALUES (%s, %s) ON CONFLICT (phone) DO NOTHING', 
                           (phone, False))
                conn.commit()
            except Exception as e:
                print(f"Kullanıcı kayıt hatası: {e}")
                conn.rollback()
            finally:
                cur.close()
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
    if not conn:
        return "Veritabanı bağlantı hatası", 500

    try:
        cur = conn.cursor()

        # Kullanıcının ID'sini al
        cur.execute('SELECT id FROM Users WHERE phone = %s', (session['phone'],))
        user_row = cur.fetchone()
        if not user_row:
            return "Kullanıcı bulunamadı", 400

        user_id = user_row[0]

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
                error_message = "⏰ Geçersiz saat. Lütfen 09:00 - 20:00 arasında bir saat seçin."
            
            # Mevcut randevu var mı kontrolü
            if not error_message:
                cur.execute('SELECT id FROM Appointments WHERE date = %s AND time = %s AND status IN (%s, %s)', 
                           (date, time, 'approved', 'pending'))
                existing = cur.fetchone()
                if existing:
                    error_message = f"⚠️ {date} - {time} saatinde zaten bir randevu mevcut."

            if error_message:
                cur.execute('SELECT id, service, date, time, status FROM Appointments WHERE user_id = %s', (user_id,))
                appointments = cur.fetchall()
                all_slots, taken_slots = get_available_slots(selected_date)
                return render_template('user_dashboard.html',
                                       appointments=appointments,
                                       today=today,
                                       selected_date=selected_date,
                                       error_message=error_message,
                                       pazar_kapali=PAZAR_KAPALI,
                                       slots=all_slots,
                                       taken=taken_slots)

            # Kayıt işlemi
            cur.execute('INSERT INTO Appointments (user_id, service, date, time, status) VALUES (%s, %s, %s, %s, %s)',
                       (user_id, service, date, time, 'pending'))
            conn.commit()
            send_email_notification(service, date, time, session['phone'])
            return redirect('/dashboard')

        # GET işlemi
        cur.execute('SELECT id, service, date, time, status FROM Appointments WHERE user_id = %s', (user_id,))
        appointments = cur.fetchall()
        all_slots, taken_slots = get_available_slots(selected_date)

        return render_template('user_dashboard.html',
                               appointments=appointments,
                               today=today,
                               selected_date=selected_date,
                               error_message=error_message,
                               pazar_kapali=PAZAR_KAPALI,
                               slots=all_slots,
                               taken=taken_slots)

    except Exception as e:
        print(f"Dashboard hatası: {e}")
        return "Bir hata oluştu", 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin')
def admin_dashboard():
    if 'phone' not in session or not session.get('is_admin'):
        return redirect('/')

    selected_date = request.args.get('date')
    today = datetime.today().strftime('%Y-%m-%d')
    tomorrow = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')

    conn = get_db()
    if not conn:
        return "Veritabanı bağlantı hatası", 500

    try:
        cur = conn.cursor()

        if selected_date == "all" or not selected_date:
            cur.execute('''SELECT a.id, u.phone, a.service, a.date, a.time, a.status 
                           FROM Appointments a JOIN Users u ON a.user_id = u.id''')
        else:
            cur.execute('''SELECT a.id, u.phone, a.service, a.date, a.time, a.status 
                           FROM Appointments a JOIN Users u ON a.user_id = u.id
                           WHERE a.date = %s''', (selected_date,))

        all_appointments = cur.fetchall()

        # Randevuları ayır - psycopg3'te tuple format
        pending = [{'id': r[0], 'phone': r[1], 'service': r[2], 'date': r[3], 'time': r[4], 'status': r[5]} 
                   for r in all_appointments if r[5] == 'pending']
        approved = [{'id': r[0], 'phone': r[1], 'service': r[2], 'date': r[3], 'time': r[4], 'status': r[5]} 
                    for r in all_appointments if r[5] == 'approved']
        rejected = [{'id': r[0], 'phone': r[1], 'service': r[2], 'date': r[3], 'time': r[4], 'status': r[5]} 
                    for r in all_appointments if r[5] == 'rejected']

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

    except Exception as e:
        print(f"Admin dashboard hatası: {e}")
        return "Bir hata oluştu", 500
    finally:
        cur.close()
        conn.close()

@app.route('/admin/week')
def admin_week():
    if 'phone' not in session or not session.get('is_admin'):
        return redirect('/')

    today = datetime.today().date()
    week_dates = [(today + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]

    conn = get_db()
    if not conn:
        return "Veritabanı bağlantı hatası", 500

    try:
        cur = conn.cursor()

        weekly_data = {}
        for day in week_dates:
            cur.execute('''SELECT a.id, u.phone, a.service, a.date, a.time, a.status 
                           FROM Appointments a JOIN Users u ON a.user_id = u.id
                           WHERE a.date = %s''', (day,))
            results = cur.fetchall()
            # Saat sıralaması - psycopg3'te tuple format
            sorted_results = sorted(results, key=lambda r: datetime.strptime(r[4], "%H:%M"))
            weekly_data[day] = sorted_results
        
        return render_template('admin_week.html', week=weekly_data)

    except Exception as e:
        print(f"Haftalık görünüm hatası: {e}")
        return "Bir hata oluştu", 500
    finally:
        cur.close()
        conn.close()

@app.route('/update/<int:id>/<status>')
def update_status(id, status):
    if 'phone' not in session or not session.get('is_admin'):
        return redirect('/')
    
    conn = get_db()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('UPDATE Appointments SET status = %s WHERE id = %s', (status, id))
            conn.commit()
        except Exception as e:
            print(f"Durum güncelleme hatası: {e}")
            conn.rollback()
        finally:
            cur.close()
            conn.close()
    
    return redirect('/admin')

@app.route('/cancel/<int:id>')
def cancel_appointment(id):
    if 'phone' not in session or session.get('is_admin'):
        return redirect('/')

    conn = get_db()
    if not conn:
        return redirect('/dashboard')

    try:
        cur = conn.cursor()

        # Önce giriş yapan kullanıcının ID'sini al
        cur.execute('SELECT id FROM Users WHERE phone = %s', (session['phone'],))
        user = cur.fetchone()
        if not user:
            return "Kullanıcı bulunamadı", 400

        user_id = user[0]

        # Randevu gerçekten bu kullanıcıya mı ait? Kontrol et
        cur.execute('SELECT id FROM Appointments WHERE id = %s AND user_id = %s', (id, user_id))
        result = cur.fetchone()

        if result:
            # İptal işlemi
            cur.execute('UPDATE Appointments SET status = %s WHERE id = %s', ('cancelled', id))
            conn.commit()

    except Exception as e:
        print(f"İptal işlemi hatası: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
    
    return redirect('/dashboard')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# Uygulama başlatıldığında veritabanını hazırla
try:
    init_database()
except Exception as e:
    print(f"Veritabanı başlatma hatası: {e}")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=port)