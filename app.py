from flask import Flask, render_template, request, redirect, session
from flask_sqlalchemy import SQLAlchemy
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import smtplib

# .env dosyasını yükle
load_dotenv()

# Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'fallback-secret-key-change-in-production')

# PostgreSQL bağlantı URI'sini oluştur
DATABASE_URL = os.getenv('DATABASE_URL')
if DATABASE_URL:
    # Render otomatik olarak DATABASE_URL sağlar
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    # Manuel konfigürasyon
    POSTGRES = {
        'user': os.getenv('DB_USER'),
        'pw': os.getenv('DB_PASSWORD'),
        'db': os.getenv('DB_NAME'),
        'host': os.getenv('DB_HOST'),
        'port': os.getenv('DB_PORT', '5432'),
    }
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{POSTGRES['user']}:{POSTGRES['pw']}@{POSTGRES['host']}:{POSTGRES['port']}/{POSTGRES['db']}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SQLAlchemy instance'ını daha güvenli şekilde oluştur
try:
    db = SQLAlchemy(app)
except Exception as e:
    print(f"Veritabanı bağlantı hatası: {e}")
    raise

PAZAR_KAPALI = True

# Model: Kullanıcı
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

# Model: Randevu
class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    service = db.Column(db.String(100), nullable=False)
    date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    time = db.Column(db.String(5), nullable=False)   # HH:MM
    status = db.Column(db.String(20), nullable=False, default='pending')

    user = db.relationship('User', backref=db.backref('appointments', lazy=True))

# Veritabanı tablolarını oluşturma
def create_tables():
    try:
        with app.app_context():
            db.create_all()
            print("✅ Veritabanı tabloları başarıyla oluşturuldu.")
    except Exception as e:
        print(f"❌ Veritabanı tablo oluşturma hatası: {e}")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# E-posta gönderme fonksiyonu
def send_email_notification(service, date, time, phone):
    try:
        sender = os.getenv("EMAIL_ADDRESS")
        receiver = os.getenv("EMAIL_RECEIVER")
        password = os.getenv("EMAIL_PASSWORD")
        
        if not all([sender, receiver, password]):
            print("❌ E-posta ayarları eksik")
            return
            
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

        # SMTP sunucusuna bağlan ve mail gönder
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print("✅ Randevu bildirimi e-posta ile gönderildi.")
    except Exception as e:
        print("❌ E-posta gönderilemedi:", e)

# Kullanıcı giriş fonksiyonu
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form['phone']
        session['phone'] = phone
        session['is_admin'] = (phone == ADMIN_PASSWORD)

        # Kullanıcıyı veritabanına kaydet veya güncelle
        try:
            user = User.query.filter_by(phone=phone).first()
            if not user:
                user = User(phone=phone, is_admin=False)
                db.session.add(user)
                db.session.commit()
        except Exception as e:
            print(f"Kullanıcı kaydetme hatası: {e}")

        return redirect('/admin' if session['is_admin'] else '/dashboard')
    return render_template('login.html')

# Kullanıcı paneli
@app.route('/dashboard', methods=['GET', 'POST'])
def user_dashboard():
    if 'phone' not in session or session.get('is_admin'):
        return redirect('/')

    today = datetime.today().strftime('%Y-%m-%d')
    selected_date = request.args.get('date') or today
    error_message = None

    # Kullanıcının bilgilerini al
    try:
        user = User.query.filter_by(phone=session['phone']).first()
        if not user:
            return "Kullanıcı bulunamadı", 400
    except Exception as e:
        print(f"Kullanıcı sorgulama hatası: {e}")
        return "Veritabanı hatası", 500

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
            try:
                existing = Appointment.query.filter_by(date=date, time=time, status="pending").first()
                if existing:
                    error_message = f"⚠️ {date} - {time} saatinde zaten bir randevu mevcut."
            except Exception as e:
                print(f"Randevu sorgulama hatası: {e}")

        if error_message:
            try:
                appointments = Appointment.query.filter_by(user_id=user.id).all()
            except Exception as e:
                appointments = []
                print(f"Randevu listesi alma hatası: {e}")
            return render_template('user_dashboard.html', appointments=appointments, error_message=error_message)

        # Yeni randevu kaydet
        try:
            new_appointment = Appointment(user_id=user.id, service=service, date=date, time=time, status='pending')
            db.session.add(new_appointment)
            db.session.commit()
            send_email_notification(service, date, time, session['phone'])
        except Exception as e:
            print(f"Randevu kaydetme hatası: {e}")
        
        return redirect('/dashboard')

    # Kullanıcı randevuları
    try:
        appointments = Appointment.query.filter_by(user_id=user.id).all()
    except Exception as e:
        appointments = []
        print(f"Randevu listesi alma hatası: {e}")
    
    return render_template('user_dashboard.html', appointments=appointments, today=today)

# Admin paneli
@app.route('/admin')
def admin_dashboard():
    if 'phone' not in session or not session.get('is_admin'):
        return redirect('/')

    selected_date = request.args.get('date')
    today = datetime.today().strftime('%Y-%m-%d')

    try:
        appointments = Appointment.query.all()
        pending = [r for r in appointments if r.status == 'pending']
        approved = [r for r in appointments if r.status == 'approved']
        rejected = [r for r in appointments if r.status == 'rejected']
    except Exception as e:
        print(f"Admin randevu listesi hatası: {e}")
        pending = approved = rejected = []

    return render_template('admin_dashboard.html', pending=pending, approved=approved, rejected=rejected, today=today)

# Admin randevu durum güncelleme
@app.route('/update/<int:id>/<status>')
def update_status(id, status):
    if 'phone' not in session or not session.get('is_admin'):
        return redirect('/')
    
    try:
        appointment = Appointment.query.get(id)
        if appointment:
            appointment.status = status
            db.session.commit()
    except Exception as e:
        print(f"Randevu güncelleme hatası: {e}")

    return redirect('/admin')

# Çıkış
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# Uygulama başlatıldığında tabloları oluştur
if __name__ == '__main__':
    create_tables()
    app.run(debug=True)
else:
    # Production ortamında (Render'da) tabloları oluştur
    create_tables()