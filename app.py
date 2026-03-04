# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response, abort, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
from functools import wraps
import os
import random
import csv
import io
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer as Serializer

load_dotenv()

app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-this')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///food_waste.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True in production with HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Email settings
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', 'noreply@foodshare.com')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', 'password')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@foodshare.com')

# File upload
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize extensions
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'
mail = Mail(app)

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'donor', 'ngo'
    organization = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    state = db.Column(db.String(50))
    zip_code = db.Column(db.String(20))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    verified = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    is_admin = db.Column(db.Boolean, default=False)
    profile_pic = db.Column(db.String(200), default='default.jpg')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Relationships
    food_listings = db.relationship('FoodListing', backref='donor', lazy=True, cascade='all, delete-orphan')
    claims = db.relationship('Claim', backref='receiver', lazy=True, cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def get_role_display(self):
        role_names = {
            'donor': 'Food Donor',
            'ngo': 'NGO/Organization'
        }
        return role_names.get(self.role, self.role)
    
    @property
    def unread_notifications(self):
        return Notification.query.filter_by(user_id=self.id, is_read=False).count()
    
    def generate_reset_token(self, expires_sec=1800):
        s = Serializer(app.config['SECRET_KEY'], expires_sec)
        return s.dumps({'user_id': self.id}).decode('utf-8')
    
    @staticmethod
    def verify_reset_token(token):
        s = Serializer(app.config['SECRET_KEY'])
        try:
            user_id = s.loads(token)['user_id']
        except:
            return None
        return User.query.get(user_id)

# Separate Admin Model
class Admin(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    role = db.Column(db.String(50), default='editor')  # 'viewer', 'editor', 'superadmin'
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def generate_reset_token(self, expires_sec=1800):
        s = Serializer(app.config['SECRET_KEY'], expires_sec)
        return s.dumps({'admin_id': self.id}).decode('utf-8')
    
    @staticmethod
    def verify_reset_token(token):
        s = Serializer(app.config['SECRET_KEY'])
        try:
            admin_id = s.loads(token)['admin_id']
        except:
            return None
        return Admin.query.get(admin_id)

class FoodListing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    food_type = db.Column(db.String(50))
    quantity = db.Column(db.Integer, nullable=False)
    location = db.Column(db.String(300), nullable=False)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    pickup_address = db.Column(db.Text)
    pickup_start = db.Column(db.DateTime, nullable=False)
    pickup_end = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='available')
    allergens = db.Column(db.Text)
    image = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    donor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    claims = db.relationship('Claim', backref='food_listing', lazy=True, cascade='all, delete-orphan')

class Claim(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    food_listing_id = db.Column(db.Integer, db.ForeignKey('food_listing.id'), nullable=False)
    ngo_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')
    pickup_time = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    people_served = db.Column(db.Integer)
    otp_code = db.Column(db.String(6), nullable=True)
    otp_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text)
    notification_type = db.Column(db.String(50))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    from_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    claim_id = db.Column(db.Integer, db.ForeignKey('claim.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Helper functions
def generate_otp():
    return ''.join([str(random.randint(0,9)) for _ in range(6)])

def send_email(recipient, subject, body):
    try:
        msg = Message(subject, recipients=[recipient], body=body)
        mail.send(msg)
        print(f"Email sent to {recipient}: {subject}")
    except Exception as e:
        print(f"Email error: {e}")
        # For development, just print the email instead of failing
        print(f"MOCK EMAIL - To: {recipient}, Subject: {subject}")
        print(f"Body: {body}")
        return True

def calculate_distance(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, sqrt, atan2
    R = 6371
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def format_datetime(value, format='%b %d, %Y %I:%M %p'):
    if value is None:
        return ""
    return value.strftime(format)

app.jinja_env.filters['datetime'] = format_datetime

# =============== ROUTES ===============

@app.route('/')
def index():
    total_donations = FoodListing.query.count()
    total_meals = db.session.query(db.func.sum(FoodListing.quantity)).scalar() or 0
    total_ngos = User.query.filter_by(role='ngo').count()
    total_donors = User.query.filter_by(role='donor').count()
    return render_template('index.html', 
                         total_donations=total_donations,
                         total_meals=total_meals,
                         total_ngos=total_ngos,
                         total_donors=total_donors)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            db.session.commit()
            flash('Login successful!', 'success')
            if user.is_admin:
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        role = request.form.get('role')
        organization = request.form.get('organization', '')
        phone = request.form.get('phone', '')
        
        errors = []
        if password != confirm_password:
            errors.append('Passwords do not match')
        if User.query.filter_by(email=email).first():
            errors.append('Email already registered')
        if User.query.filter_by(username=username).first():
            errors.append('Username already taken')
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('register.html')
        
        user = User(
            username=username,
            email=email,
            role=role,
            organization=organization if role == 'ngo' else None,
            phone=phone
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

# =============== ADMIN REGISTRATION & LOGIN ===============
@app.route('/admin/register', methods=['GET', 'POST'])
def admin_register():
    # Check if admin already logged in
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        full_name = request.form.get('full_name')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        admin_key = request.form.get('admin_key')
        
        # Admin registration key for security
        ADMIN_REGISTRATION_KEY = os.getenv('ADMIN_REGISTRATION_KEY', 'admin-secret-key')
        
        errors = []
        if admin_key != ADMIN_REGISTRATION_KEY:
            errors.append('Invalid admin registration key')
        if password != confirm_password:
            errors.append('Passwords do not match')
        if Admin.query.filter_by(email=email).first():
            errors.append('Email already registered')
        if Admin.query.filter_by(username=username).first():
            errors.append('Username already taken')
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('admin/register.html')
        
        admin = Admin(
            username=username,
            email=email,
            full_name=full_name,
            role='editor'
        )
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        flash('Admin registration successful! Please login.', 'success')
        return redirect(url_for('admin_login'))
    
    return render_template('admin/register.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    # Check if admin already logged in
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False
        
        admin = Admin.query.filter_by(email=email).first()
        if admin and admin.check_password(password) and admin.is_active:
            session['admin_id'] = admin.id
            session['admin_username'] = admin.username
            if remember:
                session.permanent = True
            admin.last_login = datetime.utcnow()
            db.session.commit()
            flash('Admin login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid email or password', 'danger')
    
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    session.pop('admin_username', None)
    flash('Admin logged out successfully.', 'info')
    return redirect(url_for('index'))

def admin_login_required(f):
    """Decorator to require admin login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please login as admin first', 'warning')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'donor':
        return redirect(url_for('donor_dashboard'))
    elif current_user.role == 'ngo':
        return redirect(url_for('ngo_dashboard'))
    else:
        flash('Invalid user role', 'danger')
        return redirect(url_for('index'))

# =============== PROFILE ===============
@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.username = request.form.get('username')
        current_user.email = request.form.get('email')
        current_user.phone = request.form.get('phone')
        current_user.organization = request.form.get('organization')
        current_user.address = request.form.get('address')
        # Handle profile picture
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file.filename != '':
                filename = secure_filename(f"user_{current_user.id}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                current_user.profile_pic = filename
        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html', user=current_user)

@app.route('/change_password', methods=['POST'])
@login_required
def change_password():
    old = request.form.get('old_password')
    new = request.form.get('new_password')
    if current_user.check_password(old):
        current_user.set_password(new)
        db.session.commit()
        flash('Password changed.', 'success')
    else:
        flash('Incorrect old password.', 'danger')
    return redirect(url_for('profile'))

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            token = user.generate_reset_token()
            reset_url = url_for('reset_password', token=token, _external=True)
            send_email(user.email, 'Password Reset',
                       f'Click the link to reset your password: {reset_url}')
            flash('Reset link sent to your email.', 'info')
        else:
            flash('Email not found.', 'danger')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = User.verify_reset_token(token)
    if not user:
        flash('Invalid or expired token.', 'danger')
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password')
        user.set_password(password)
        db.session.commit()
        flash('Password reset. Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_password.html')

# =============== DONOR ROUTES ===============
@app.route('/donor/dashboard')
@login_required
def donor_dashboard():
    if current_user.role != 'donor':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    listings = FoodListing.query.filter_by(donor_id=current_user.id)\
        .order_by(FoodListing.created_at.desc()).limit(5).all()
    claims = Claim.query.join(FoodListing)\
        .filter(FoodListing.donor_id == current_user.id)\
        .order_by(Claim.created_at.desc()).limit(5).all()
    total_listings = FoodListing.query.filter_by(donor_id=current_user.id).count()
    available_listings = FoodListing.query.filter_by(donor_id=current_user.id, status='available').count()
    claimed_listings = FoodListing.query.filter_by(donor_id=current_user.id, status='claimed').count()
    total_meals_result = db.session.query(db.func.sum(FoodListing.quantity))\
        .filter(FoodListing.donor_id == current_user.id, FoodListing.status == 'picked_up').first()
    total_meals = total_meals_result[0] or 0 if total_meals_result else 0
    return render_template('donor/donor_dashboard.html',
                         listings=listings,
                         claims=claims,
                         total_listings=total_listings,
                         available_listings=available_listings,
                         claimed_listings=claimed_listings,
                         total_meals=total_meals)

@app.route('/create_listing', methods=['GET', 'POST'])
@login_required
def create_listing():
    if current_user.role != 'donor':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    copy_id = request.args.get('copy_from')
    copy_listing = None
    if copy_id:
        copy_listing = FoodListing.query.get(copy_id)
        if copy_listing and copy_listing.donor_id != current_user.id:
            copy_listing = None

    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        food_type = request.form.get('food_type')
        quantity = int(request.form.get('quantity'))
        location = request.form.get('location')
        pickup_address = request.form.get('pickup_address')
        pickup_start_str = request.form.get('pickup_start')
        pickup_end_str = request.form.get('pickup_end')
        allergens = request.form.get('allergens', '')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        
        pickup_start = datetime.fromisoformat(pickup_start_str.replace('Z', '+00:00'))
        pickup_end = datetime.fromisoformat(pickup_end_str.replace('Z', '+00:00'))
        
        listing = FoodListing(
            title=title,
            description=description,
            food_type=food_type,
            quantity=quantity,
            location=location,
            pickup_address=pickup_address,
            pickup_start=pickup_start,
            pickup_end=pickup_end,
            allergens=allergens,
            donor_id=current_user.id
        )
        
        # Add GPS coordinates if provided
        if latitude and longitude:
            listing.latitude = float(latitude)
            listing.longitude = float(longitude)
        
        # Handle image upload
        if 'image' in request.files:
            file = request.files['image']
            if file.filename != '':
                filename = secure_filename(f"food_{datetime.utcnow().timestamp()}_{file.filename}")
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                listing.image = filename
        
        db.session.add(listing)
        db.session.commit()
        
        # Notify NGOs (optional, can be heavy)
        flash('Food listing created successfully!', 'success')
        return redirect(url_for('donor_dashboard'))
    
    now = datetime.utcnow()
    default_start = now.strftime('%Y-%m-%dT%H:%M')
    default_end = (now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')
    return render_template('donor/create_listing.html',
                         default_start=default_start,
                         default_end=default_end,
                         copy=copy_listing)

@app.route('/my_listings')
@login_required
def my_listings():
    if current_user.role != 'donor':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    listings = FoodListing.query.filter_by(donor_id=current_user.id)\
        .order_by(FoodListing.created_at.desc()).all()
    return render_template('donor/my_listings.html', listings=listings)

@app.route('/listing/<int:listing_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_listing(listing_id):
    if current_user.role != 'donor':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    listing = FoodListing.query.get_or_404(listing_id)
    if listing.donor_id != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('my_listings'))
    if listing.status != 'available':
        flash('Only available listings can be edited', 'warning')
        return redirect(url_for('my_listings'))
    if request.method == 'POST':
        listing.title = request.form.get('title')
        listing.quantity = int(request.form.get('quantity'))
        pickup_end_str = request.form.get('pickup_end')
        listing.pickup_end = datetime.fromisoformat(pickup_end_str.replace('Z', '+00:00'))
        db.session.commit()
        flash('Listing updated successfully!', 'success')
        return redirect(url_for('my_listings'))
    return render_template('donor/edit_listing.html', listing=listing)

@app.route('/listing/<int:listing_id>/delete', methods=['POST'])
@login_required
def delete_listing(listing_id):
    if current_user.role != 'donor':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    listing = FoodListing.query.get_or_404(listing_id)
    if listing.donor_id != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('my_listings'))
    Claim.query.filter_by(food_listing_id=listing.id).delete()
    db.session.delete(listing)
    db.session.commit()
    flash('Food listing deleted successfully!', 'success')
    return redirect(url_for('donor_dashboard'))

@app.route('/listing/<int:listing_id>/view')
@login_required
def view_listing(listing_id):
    listing = FoodListing.query.get_or_404(listing_id)
    if current_user.role == 'donor' and listing.donor_id != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('donor_dashboard'))
    return render_template('donor/view_listing.html', listing=listing)

# =============== NGO ROUTES ===============
@app.route('/ngo/dashboard')
@login_required
def ngo_dashboard():
    if current_user.role != 'ngo':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    available_listings = FoodListing.query.filter_by(status='available')\
        .filter(FoodListing.pickup_end > datetime.utcnow())\
        .order_by(FoodListing.created_at.desc()).limit(3).all()
    my_claims = Claim.query.filter_by(ngo_id=current_user.id)\
        .order_by(Claim.created_at.desc()).limit(5).all()
    total_claims = Claim.query.filter_by(ngo_id=current_user.id).count()
    active_claims = Claim.query.filter_by(ngo_id=current_user.id)\
        .filter(Claim.status.in_(['pending', 'confirmed'])).count()
    completed_claims = Claim.query.filter_by(ngo_id=current_user.id, status='picked_up')\
        .join(FoodListing).all()
    total_meals_claimed = sum(claim.food_listing.quantity for claim in completed_claims)
    return render_template('ngo/ngo_dashboard.html',
                         available_listings=available_listings,
                         my_claims=my_claims,
                         total_claims=total_claims,
                         active_claims=active_claims,
                         total_meals_claimed=total_meals_claimed)

@app.route('/available_food')
@login_required
def available_food():
    if current_user.role != 'ngo':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    listings = FoodListing.query.filter_by(status='available')\
        .filter(FoodListing.pickup_end > datetime.utcnow())\
        .order_by(FoodListing.created_at.desc()).all()
    
    # Calculate distances if NGO has coordinates
    if current_user.latitude and current_user.longitude:
        for listing in listings:
            if listing.latitude and listing.longitude:
                listing.distance = calculate_distance(
                    current_user.latitude, current_user.longitude,
                    listing.latitude, listing.longitude
                )
            else:
                listing.distance = float('inf')
        
        # Sort by distance (closest first)
        listings.sort(key=lambda x: x.distance)
    
    return render_template('ngo/available_food.html', 
                         listings=listings,
                         datetime=datetime)

@app.route('/claim/<int:listing_id>', methods=['POST'])
@login_required
def claim_food(listing_id):
    if current_user.role != 'ngo':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    # Atomic lock to prevent double claim
    listing = FoodListing.query.filter_by(id=listing_id, status='available').with_for_update().first()
    if not listing:
        flash('This listing is no longer available', 'danger')
        return redirect(url_for('available_food'))
    
    existing_claim = Claim.query.filter_by(
        food_listing_id=listing_id,
        ngo_id=current_user.id
    ).first()
    if existing_claim:
        flash('You have already claimed this listing', 'warning')
        return redirect(url_for('ngo_dashboard'))
    
    claim = Claim(
        food_listing_id=listing_id,
        ngo_id=current_user.id,
        status='pending'
    )
    listing.status = 'claimed'
    
    notification = Notification(
        user_id=listing.donor_id,
        title='Food Claimed!',
        message=f'{current_user.organization or current_user.username} has claimed your listing: {listing.title}',
        notification_type='claim_update'
    )
    db.session.add(claim)
    db.session.add(notification)
    db.session.commit()
    
    # Send email
    send_email(listing.donor.email,
               'Food Claimed',
               f'{current_user.organization} has claimed {listing.title}. Please coordinate pickup.')
    
    flash('Food claimed successfully! Please contact the donor for pickup.', 'success')
    return redirect(url_for('ngo_dashboard'))

@app.route('/my_claims')
@login_required
def my_claims():
    if current_user.role != 'ngo':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    claims = Claim.query.filter_by(ngo_id=current_user.id)\
        .order_by(Claim.created_at.desc()).all()
    return render_template('ngo/my_claims.html', 
                         claims=claims,
                         datetime=datetime)

@app.route('/claim/<int:claim_id>/update_status', methods=['POST'])
@login_required
def update_claim_status(claim_id):
    print(f"Update status called for claim {claim_id} by user {current_user.id}")
    if current_user.role != 'ngo':
        print("Permission denied: not NGO")
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    claim = Claim.query.get_or_404(claim_id)
    if claim.ngo_id != current_user.id:
        print("Permission denied: not claim owner")
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    data = request.get_json()
    print(f"Received data: {data}")
    new_status = data.get('status')
    notes = data.get('notes', '')
    people_served = data.get('people_served')
    
    if new_status in ['confirmed', 'picked_up', 'cancelled']:
        print(f"Updating claim status to: {new_status}")
        claim.status = new_status
        claim.notes = notes
        
        if new_status == 'confirmed':
            claim.otp_code = generate_otp()
            print(f"Generated OTP: {claim.otp_code} for claim {claim.id}")  # Debug line
            # Notify donor
            notification = Notification(
                user_id=claim.food_listing.donor_id,
                title='Claim Confirmed',
                message=f'{current_user.organization} has confirmed pickup for {claim.food_listing.title}. OTP: {claim.otp_code}',
                notification_type='claim_update'
            )
            db.session.add(notification)
            send_email(claim.food_listing.donor.email,
                       'Pickup Confirmed',
                       f'NGO {current_user.organization} confirmed they will pick up {claim.food_listing.title}. OTP: {claim.otp_code}')
        elif new_status == 'picked_up':
            claim.pickup_time = datetime.utcnow()
            claim.food_listing.status = 'picked_up'
            claim.people_served = people_served or claim.food_listing.quantity
            notification = Notification(
                user_id=claim.food_listing.donor_id,
                title='Food Picked Up!',
                message=f'{current_user.organization} has picked up {claim.food_listing.title}',
                notification_type='claim_update'
            )
            db.session.add(notification)
        elif new_status == 'cancelled':
            claim.food_listing.status = 'available'
        
        db.session.commit()
        print("Database committed successfully")
        return jsonify({'success': True})
    print(f"Invalid status: {new_status}")
    return jsonify({'success': False, 'error': 'Invalid status'}), 400

@app.route('/claim/<int:claim_id>/reject', methods=['POST'])
@login_required
def reject_claim(claim_id):
    if current_user.role != 'donor':
        abort(403)
    claim = Claim.query.get_or_404(claim_id)
    if claim.food_listing.donor_id != current_user.id:
        abort(403)
    if claim.status in ['pending', 'confirmed']:
        claim.status = 'cancelled'
        claim.food_listing.status = 'available'
        db.session.commit()
        flash('Claim rejected.', 'success')
    else:
        flash('Cannot reject this claim.', 'danger')
    return redirect(url_for('donor_dashboard'))

@app.route('/claim/<int:claim_id>/verify_otp', methods=['POST'])
@login_required
def verify_otp(claim_id):
    claim = Claim.query.get_or_404(claim_id)
    if claim.food_listing.donor_id != current_user.id:
        abort(403)
    data = request.get_json()
    if data.get('otp') == claim.otp_code:
        claim.status = 'picked_up'
        claim.pickup_time = datetime.utcnow()
        claim.food_listing.status = 'picked_up'
        claim.otp_verified = True
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid OTP'}), 400

# =============== ADMIN ROUTES ===============
@app.route('/admin')
@admin_login_required
def admin_dashboard():
    admin_id = session.get('admin_id')
    admin = Admin.query.get(admin_id)
    total_users = User.query.count()
    total_listings = FoodListing.query.count()
    total_claims = Claim.query.count()
    pending_ngos = User.query.filter_by(role='ngo', verified=False).count()
    return render_template('admin/dashboard.html',
                           admin=admin,
                           total_users=total_users,
                           total_listings=total_listings,
                           total_claims=total_claims,
                           pending_ngos=pending_ngos)

@app.route('/admin/users')
@admin_login_required
def admin_users():
    users = User.query.all()
    return render_template('admin/user.html', users=users)

@app.route('/admin/verify_ngo/<int:user_id>', methods=['POST'])
@admin_login_required
def verify_ngo(user_id):
    user = User.query.get_or_404(user_id)
    user.verified = True
    db.session.commit()
    flash(f'{user.organization} verified.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/toggle_user/<int:user_id>', methods=['POST'])
@admin_login_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_active = not user.is_active
    db.session.commit()
    flash(f'User {user.username} {"activated" if user.is_active else "deactivated"}.', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/listings')
@admin_login_required
def admin_listings():
    listings = FoodListing.query.order_by(FoodListing.created_at.desc()).all()
    return render_template('admin/listing.html', listings=listings)

@app.route('/admin/delete_listing/<int:listing_id>', methods=['POST'])
@admin_login_required
def admin_delete_listing(listing_id):
    listing = FoodListing.query.get_or_404(listing_id)
    db.session.delete(listing)
    db.session.commit()
    flash('Listing deleted.', 'success')
    return redirect(url_for('admin_listings'))

@app.route('/admin/export')
@admin_login_required
def export_data():
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['ID', 'Title', 'Quantity', 'Status', 'Donor', 'Created At'])
    listings = FoodListing.query.all()
    for l in listings:
        cw.writerow([l.id, l.title, l.quantity, l.status, l.donor.username, l.created_at])
    output = si.getvalue()
    response = make_response(output)
    response.headers["Content-Disposition"] = "attachment; filename=food_listings.csv"
    response.headers["Content-type"] = "text/csv"
    return response

# =============== API ENDPOINTS ===============
@app.route('/api/notifications')
@login_required
def get_notifications():
    notifications = Notification.query.filter_by(user_id=current_user.id, is_read=False)\
        .order_by(Notification.created_at.desc()).limit(10).all()
    return jsonify({
        'count': len(notifications),
        'notifications': [{
            'id': n.id,
            'title': n.title,
            'message': n.message,
            'created_at': n.created_at.isoformat()
        } for n in notifications]
    })

@app.route('/api/notifications/mark_read/<int:notification_id>', methods=['POST'])
@login_required
def mark_notification_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    if notification.user_id != current_user.id:
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    notification.is_read = True
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/listings/<int:listing_id>', methods=['DELETE'])
@login_required
def delete_listing_api(listing_id):
    listing = FoodListing.query.get_or_404(listing_id)
    if listing.donor_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403
    if listing.status != 'available':
        return jsonify({'success': False, 'error': 'Only available listings can be deleted'}), 400
    Claim.query.filter_by(food_listing_id=listing_id).delete()
    db.session.delete(listing)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/stats/total_meals')
def get_total_meals():
    total_meals = db.session.query(db.func.sum(FoodListing.quantity)).scalar() or 0
    total_donations = FoodListing.query.count()
    return jsonify({
        'total_meals': total_meals,
        'total_donations': total_donations,
        'total_claims': Claim.query.count(),
        'completed_claims': Claim.query.filter_by(status='picked_up').count()
    })

@app.route('/api/listings/available')
@login_required
def get_available_listings():
    listings = FoodListing.query.filter_by(status='available')\
        .filter(FoodListing.pickup_end > datetime.utcnow())\
        .order_by(FoodListing.created_at.desc()).all()
    result = []
    for listing in listings:
        result.append({
            'id': listing.id,
            'title': listing.title,
            'description': listing.description,
            'food_type': listing.food_type,
            'quantity': listing.quantity,
            'location': listing.location,
            'pickup_address': listing.pickup_address,
            'pickup_start': listing.pickup_start.isoformat(),
            'pickup_end': listing.pickup_end.isoformat(),
            'allergens': listing.allergens,
            'image': listing.image,
            'donor': {
                'id': listing.donor.id,
                'organization': listing.donor.organization or listing.donor.username,
                'email': listing.donor.email,
                'phone': listing.donor.phone
            }
        })
    return jsonify({'listings': result})

# =============== ERROR HANDLERS ===============
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

@app.errorhandler(403)
def forbidden_error(error):
    return render_template('403.html'), 403

# =============== HELPER PAGES ===============
@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/food-safety')
def food_safety():
    return render_template('food_safety.html')

# =============== UTILITY ===============
def cleanup_expired_listings():
    expired_listings = FoodListing.query.filter(
        FoodListing.status == 'available',
        FoodListing.pickup_end < datetime.utcnow()
    ).all()
    for listing in expired_listings:
        listing.status = 'expired'
    if expired_listings:
        db.session.commit()

# =============== INIT ===============
db_initialized = False

@app.before_request
def init_db():
    global db_initialized
    if not db_initialized:
        db.create_all()
        # Create admin user if not exists
        admin_email = os.getenv('ADMIN_EMAIL', 'admin@example.com')
        admin = User.query.filter_by(email=admin_email).first()
        if not admin:
            admin = User(
                username='admin',
                email=admin_email,
                role='donor',
                is_admin=True,
                verified=True
            )
            admin.set_password(os.getenv('ADMIN_PASSWORD', 'admin123'))
            db.session.add(admin)
            db.session.commit()
        db_initialized = True
    cleanup_expired_listings()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)