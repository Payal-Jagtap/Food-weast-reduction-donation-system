# app.py
# Module 0 - Common Foundation imports - Authentication & User Profile
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response, abort, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import inspect, text
from sqlalchemy.orm import aliased
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone
from functools import wraps
import os
import random
import csv
import io
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer as Serializer

# Module 0 - Load environment variables for configuration
load_dotenv()

# Module 0 - Initialize Flask application
app = Flask(__name__)

# Module 0 - Configuration settings for the application
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-this')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///food_waste.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True in production with HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Module 0 - Email configuration settings
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME', 'noreply@foodshare.com')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', 'password')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', 'noreply@foodshare.com')

# Module 0 - File upload configuration
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Module 0 - Initialize Flask extensions
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'
mail = Mail(app)

# Module 0 - Database Models for User Authentication and Profiles
class User(UserMixin, db.Model):
    # Module 0 - Base user authentication fields
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'donor', 'ngo'
    verified = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    profile_pic = db.Column(db.String(200), default='default.jpg')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Module 0 - User relationships
    food_listings = db.relationship('FoodListing', backref='donor', lazy=True, cascade='all, delete-orphan')
    claims = db.relationship('Claim', backref='receiver', lazy=True, cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')
    
    # Module 0 - User authentication methods
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
    def is_admin(self):
        return False
    
    @property
    def organization(self):
        if self.role == 'ngo' and self.ngo_profile:
            return self.ngo_profile.organization
        if self.role == 'donor' and self.donor_profile:
            return self.donor_profile.organization
        return None

    @property
    def latitude(self):
        if self.ngo_profile and self.ngo_profile.latitude is not None:
            return self.ngo_profile.latitude
        if self.donor_profile and self.donor_profile.latitude is not None:
            return self.donor_profile.latitude
        return None

    @property
    def longitude(self):
        if self.ngo_profile and self.ngo_profile.longitude is not None:
            return self.ngo_profile.longitude
        if self.donor_profile and self.donor_profile.longitude is not None:
            return self.donor_profile.longitude
        return None
    
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
        return db.session.get(User, user_id)

# Module 0 - Donor Profile Table - Separate table for donor-specific information
class Donor(db.Model):
    # Module 0 - Donor profile fields
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    state = db.Column(db.String(50))
    zip_code = db.Column(db.String(20))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Module 0 - Relationship with User model
    user = db.relationship('User', backref=db.backref('donor_profile', uselist=False, cascade='all, delete-orphan'))

# Module 0 - NGO Profile Table - Separate table for NGO-specific information  
class NGO(db.Model):
    # Module 0 - NGO profile fields
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    organization = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    state = db.Column(db.String(50))
    zip_code = db.Column(db.String(20))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    verified = db.Column(db.Boolean, default=False)
    registration_number = db.Column(db.String(100))
    ngo_type = db.Column(db.String(100))  # e.g., 'Charity', 'Community Center', 'Shelter'
    capacity = db.Column(db.Integer)  # Number of people they can serve
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Module 0 - Relationship with User model
    user = db.relationship('User', backref=db.backref('ngo_profile', uselist=False, cascade='all, delete-orphan'))

# Module 0 - Admin authentication model for admin users
class Admin(UserMixin, db.Model):
    # Module 0 - Admin authentication fields
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
    
    # Module 0 - Admin authentication methods
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
        return db.session.get(Admin, admin_id)

# Module 1 - Donor Operations: Food Listing Model
class FoodListing(db.Model):
    # Module 1 - Food donation fields
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
    
    # Module 1 - Food listing relationships
    donor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    claims = db.relationship('Claim', backref='food_listing', lazy=True, cascade='all, delete-orphan')

# Module 3 - Transaction & Verification: Claim Model
class Claim(db.Model):
    # Module 3 - Claim transaction fields
    id = db.Column(db.Integer, primary_key=True)
    food_listing_id = db.Column(db.Integer, db.ForeignKey('food_listing.id'), nullable=False)
    ngo_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')
    pickup_time = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    people_served = db.Column(db.Integer)
    otp_code = db.Column(db.String(4), nullable=True)
    otp_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Module 0 - Global Notification Model
class Notification(db.Model):
    # Module 0 - Notification fields
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text)
    notification_type = db.Column(db.String(50))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Module 4 - Fulfillment & Logistics: Review Model
class Review(db.Model):
    # Module 4 - Review fields for fulfillment feedback
    id = db.Column(db.Integer, primary_key=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    from_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    claim_id = db.Column(db.Integer, db.ForeignKey('claim.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Module 0 - User loader for authentication system
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Helper functions
def generate_otp():
    return ''.join([str(random.randint(0,9)) for _ in range(4)])

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
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime(format)

app.jinja_env.filters['datetime'] = format_datetime

# =============== VALIDATION FUNCTIONS ===============

def validate_email(email):
    """
    Validate email format
    Returns: (is_valid, error_message)
    """
    import re
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_regex, email):
        return False, 'Invalid email format. Please enter a valid email address.'
    if len(email) > 120:
        return False, 'Email is too long (maximum 120 characters).'
    return True, None

def validate_phone(phone):
    """
    Validate phone number - must be 10 digits, no extensions
    Returns: (is_valid, error_message)
    """
    import re
    if not phone:  # Phone is optional
        return True, None
    
    # Remove common separators
    cleaned_phone = re.sub(r'[\s\-\.\(\)]+', '', phone)
    
    # Check if it's exactly 10 digits
    if not cleaned_phone.isdigit():
        return False, 'Phone number must contain only digits (no letters or special characters).'
    
    if len(cleaned_phone) != 10:
        return False, 'Phone number must be exactly 10 digits (no extensions).'
    
    return True, None

def validate_password(password):
    """
    Validate password strength:
    - Minimum 8 characters
    - At least one uppercase letter (A-Z)
    - At least one symbol/special character (!@#$%^&* etc)
    Returns: (is_valid, error_message)
    """
    import re
    
    if len(password) < 8:
        return False, 'Password must be at least 8 characters long.'
    
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter (A-Z).'
    
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'",.<>?/\\|`~]', password):
        return False, 'Password must contain at least one special character (!@#$%^&* etc).'
    
    return True, None

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
        address = request.form.get('address', '')
        city = request.form.get('city', '')
        state = request.form.get('state', '')
        zip_code = request.form.get('zip_code', '')
        
        errors = []
        
        # Email validation
        email_valid, email_error = validate_email(email)
        if not email_valid:
            errors.append(email_error)
        
        # Password validation
        password_valid, password_error = validate_password(password)
        if not password_valid:
            errors.append(password_error)
        
        # Phone validation (only if provided)
        if phone:
            phone_valid, phone_error = validate_phone(phone)
            if not phone_valid:
                errors.append(phone_error)
        
        # Password match check
        if password != confirm_password:
            errors.append('Passwords do not match')
        
        # Duplicate email check
        if User.query.filter_by(email=email).first():
            errors.append('Email already registered')
        
        # Duplicate username check
        if User.query.filter_by(username=username).first():
            errors.append('Username already taken')
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('register.html')
        
        # Create base user
        user = User(
            username=username,
            email=email,
            role=role
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        # Create role-specific profile
        if role == 'donor':
            donor_profile = Donor(
                user_id=user.id,
                organization=organization,
                phone=phone,
                address=address,
                city=city,
                state=state,
                zip_code=zip_code
            )
            db.session.add(donor_profile)
        elif role == 'ngo':
            ngo_profile = NGO(
                user_id=user.id,
                organization=organization,
                phone=phone,
                address=address,
                city=city,
                state=state,
                zip_code=zip_code
            )
            db.session.add(ngo_profile)
        
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
        phone = request.form.get('phone', '')
        
        # Validate phone if provided
        if phone:
            phone_valid, phone_error = validate_phone(phone)
            if not phone_valid:
                flash(phone_error, 'danger')
                return render_template('profile.html', user=current_user)
        
        # Update base user info
        current_user.username = request.form.get('username')
        current_user.email = request.form.get('email')
        
        # Update role-specific profile
        if current_user.role == 'donor':
            donor_profile = current_user.donor_profile
            if donor_profile:
                donor_profile.organization = request.form.get('organization')
                donor_profile.phone = phone
                donor_profile.address = request.form.get('address')
                donor_profile.city = request.form.get('city')
                donor_profile.state = request.form.get('state')
                donor_profile.zip_code = request.form.get('zip_code')
                donor_profile.latitude = float(request.form.get('latitude', 0)) if request.form.get('latitude') else None
                donor_profile.longitude = float(request.form.get('longitude', 0)) if request.form.get('longitude') else None
        elif current_user.role == 'ngo':
            ngo_profile = current_user.ngo_profile
            if ngo_profile:
                ngo_profile.organization = request.form.get('organization')
                ngo_profile.phone = phone
                ngo_profile.address = request.form.get('address')
                ngo_profile.city = request.form.get('city')
                ngo_profile.state = request.form.get('state')
                ngo_profile.zip_code = request.form.get('zip_code')
                ngo_profile.latitude = float(request.form.get('latitude', 0)) if request.form.get('latitude') else None
                ngo_profile.longitude = float(request.form.get('longitude', 0)) if request.form.get('longitude') else None
                ngo_profile.registration_number = request.form.get('registration_number')
                ngo_profile.ngo_type = request.form.get('ngo_type')
                ngo_profile.capacity = int(request.form.get('capacity', 0)) if request.form.get('capacity') else None
        
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
    cleanup_expired_listings()
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
    review_count = Review.query.filter_by(to_user_id=current_user.id).count()
    average_rating = db.session.query(db.func.avg(Review.rating))\
        .filter(Review.to_user_id == current_user.id).scalar() or 0
    reviews = Review.query.filter_by(to_user_id=current_user.id)\
        .order_by(Review.created_at.desc()).limit(5).all()
    review_rows = db.session.query(
        FoodListing.id.label('listing_id'),
        Review.rating,
        Review.comment,
        Review.created_at,
        User.username.label('author_username'),
        NGO.organization.label('author_organization')
    ).join(Claim, Claim.id == Review.claim_id)\
     .join(FoodListing, FoodListing.id == Claim.food_listing_id)\
     .outerjoin(User, User.id == Review.from_user_id)\
     .outerjoin(NGO, NGO.user_id == User.id)\
     .filter(FoodListing.donor_id == current_user.id)\
     .order_by(Review.created_at.desc()).all()

    listing_reviews = {}
    for row in review_rows:
        listing_reviews.setdefault(row.listing_id, []).append({
            'rating': row.rating,
            'comment': row.comment,
            'created_at': row.created_at,
            'author': row.author_organization or row.author_username or 'Anonymous'
        })
    return render_template('donor/donor_dashboard.html',
                         listings=listings,
                         claims=claims,
                         total_listings=total_listings,
                         available_listings=available_listings,
                         claimed_listings=claimed_listings,
                         total_meals=total_meals,
                         review_count=review_count,
                         average_rating=average_rating,
                         reviews=reviews,
                         listing_reviews=listing_reviews)

@app.route('/create_listing', methods=['GET', 'POST'])
@login_required
def create_listing():
    if current_user.role != 'donor':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    
    copy_id = request.args.get('copy_from')
    copy_listing = None
    if copy_id:
        copy_listing = db.session.get(FoodListing, copy_id)
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
        
        # Auto-generate pickup times if not provided
        if pickup_start_str and pickup_end_str:
            pickup_start = datetime.fromisoformat(pickup_start_str.replace('Z', '+00:00'))
            pickup_end = datetime.fromisoformat(pickup_end_str.replace('Z', '+00:00'))
        else:
            # Automatic: Start = now, End = +2 hours (using local time)
            pickup_start = datetime.now()
            pickup_end = pickup_start + timedelta(hours=2)
        
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

        # Notify all verified NGOs that a new listing is available
        verified_ngos = User.query.filter_by(role='ngo', verified=True, is_active=True).all()
        for ngo in verified_ngos:
            db.session.add(Notification(
                user_id=ngo.id,
                title='New Food Available',
                message=f'New food listing added: {listing.title} at {listing.location}',
                notification_type='system'
            ))

        db.session.commit()
        
        # Notify NGOs (optional, can be heavy)
        flash('Food listing created successfully!', 'success')
        return redirect(url_for('donor_dashboard'))
    
    now = datetime.now()  # Use local time instead of UTC
    start_time = now
    end_time = start_time + timedelta(hours=2)
    default_start = start_time.strftime('%Y-%m-%dT%H:%M')
    default_end = end_time.strftime('%Y-%m-%dT%H:%M')
    
    # Also create formatted display times
    display_start = start_time.strftime('%b %d, %Y %I:%M %p')
    display_end = end_time.strftime('%b %d, %Y %I:%M %p')
    
    return render_template('donor/create_listing.html',
                         default_start=default_start,
                         default_end=default_end,
                         display_start=display_start,
                         display_end=display_end,
                         copy=copy_listing)

@app.route('/my_listings')
@login_required
def my_listings():
    if current_user.role != 'donor':
        flash('Access denied', 'danger')
        return redirect(url_for('dashboard'))
    cleanup_expired_listings()
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
    cleanup_expired_listings()
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
    cleanup_expired_listings()
    available_cutoff = datetime.utcnow() - timedelta(hours=2)
    available_listings = FoodListing.query.filter_by(status='available')\
        .filter(FoodListing.created_at >= available_cutoff)\
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
    cleanup_expired_listings()
    available_cutoff = datetime.utcnow() - timedelta(hours=2)
    
    listings = FoodListing.query.filter_by(status='available')\
        .filter(FoodListing.created_at >= available_cutoff)\
        .order_by(FoodListing.created_at.desc()).all()
    for listing in listings:
        listing.hours_left = max(
            0,
            int(((listing.created_at + timedelta(hours=2)) - datetime.utcnow()).total_seconds() / 3600)
        )
    
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
    cleanup_expired_listings()
    
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
        status='pending',
        otp_code=generate_otp()  # Generate OTP on claim
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
               f'{current_user.organization or current_user.username} has claimed {listing.title}. Please coordinate pickup.')
    
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
    otp = (data.get('otp') or '').strip()
    
    if new_status in ['confirmed', 'picked_up', 'cancelled']:
        print(f"Updating claim status to: {new_status}")
        claim.notes = notes
        
        if new_status == 'confirmed':
            claim.status = new_status
            # Notify donor (without OTP)
            notification = Notification(
                user_id=claim.food_listing.donor_id,
                title='Claim Confirmed',
                message=f'{current_user.organization or current_user.username} has confirmed pickup for {claim.food_listing.title}',
                notification_type='claim_update'
            )
            db.session.add(notification)
            send_email(claim.food_listing.donor.email,
                       'Pickup Confirmed',
                       f'NGO {current_user.organization or current_user.username} confirmed they will pick up {claim.food_listing.title}.')
        elif new_status == 'picked_up':
            if not otp:
                return jsonify({'success': False, 'error': 'OTP is required when marking a claim as picked up'}), 400
            if otp != claim.otp_code:
                return jsonify({'success': False, 'error': 'Invalid OTP'}), 400
            claim.status = new_status
            claim.pickup_time = datetime.utcnow()
            claim.food_listing.status = 'picked_up'
            claim.people_served = people_served or claim.food_listing.quantity
            claim.otp_verified = True
            notification = Notification(
                user_id=claim.food_listing.donor_id,
                title='Food Picked Up!',
                message=f'{current_user.organization or current_user.username} has picked up {claim.food_listing.title}',
                notification_type='claim_update'
            )
            db.session.add(notification)
            donor_notification = Notification(
                user_id=claim.food_listing.donor_id,
                title='Pickup Completed',
                message=f'Pickup completed for {claim.food_listing.title}.',
                notification_type='claim_update'
            )
            db.session.add(donor_notification)
        elif new_status == 'cancelled':
            claim.status = new_status
            claim.food_listing.status = 'available'
            donor_notification = Notification(
                user_id=claim.food_listing.donor_id,
                title='Claim Cancelled',
                message=f'The claim for {claim.food_listing.title} was cancelled by the NGO. Your listing is available again.',
                notification_type='claim_update'
            )
            db.session.add(donor_notification)
        
        db.session.commit()
        print("Database committed successfully")
        return jsonify({'success': True})
    print(f"Invalid status: {new_status}")
    return jsonify({'success': False, 'error': 'Invalid status'}), 400


@app.route('/api/reviews', methods=['POST'])
@login_required
def create_review():
    data = request.get_json(silent=True) or {}
    claim_id = data.get('claim_id')
    rating = data.get('rating')
    comment = (data.get('comment') or '').strip()

    if current_user.role != 'ngo':
        return jsonify({'success': False, 'error': 'Only NGOs can submit reviews'}), 403

    if not claim_id:
        return jsonify({'success': False, 'error': 'Claim is required'}), 400

    claim = Claim.query.get_or_404(claim_id)
    if claim.ngo_id != current_user.id:
        return jsonify({'success': False, 'error': 'Permission denied'}), 403

    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Rating must be a number'}), 400

    if rating < 1 or rating > 5:
        return jsonify({'success': False, 'error': 'Rating must be between 1 and 5'}), 400

    review = Review(
        rating=rating,
        comment=comment,
        from_user_id=current_user.id,
        to_user_id=claim.food_listing.donor_id,
        claim_id=claim.id
    )
    db.session.add(review)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Review submitted successfully'})

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

    if (datetime.utcnow() - claim.created_at).total_seconds() > CLAIM_OTP_EXPIRY_SECONDS:
        return jsonify({'success': False, 'message': 'OTP expired'})
    
    data = request.get_json()
    if data.get('otp') == claim.otp_code:
        claim.status = 'picked_up'
        claim.pickup_time = datetime.utcnow()
        claim.food_listing.status = 'picked_up'
        claim.otp_verified = True
        db.session.commit()
        
        # Create notification for NGO
        notification = Notification(
            user_id=claim.ngo_id,
            title='Pickup Verified!',
            message=f'Your pickup for {claim.food_listing.title} has been verified by the donor.',
            notification_type='claim_update'
        )
        db.session.add(notification)
        donor_notification = Notification(
            user_id=claim.food_listing.donor_id,
            title='Pickup Completed',
            message=f'Pickup completed for {claim.food_listing.title}.',
            notification_type='claim_update'
        )
        db.session.add(donor_notification)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'OTP verified successfully'})
    else:
        return jsonify({'success': False, 'message': 'Invalid OTP'})

@app.route('/verify_otp/<int:claim_id>', methods=['GET', 'POST'])
@login_required
def verify_otp_page(claim_id):
    claim = Claim.query.get_or_404(claim_id)
    if claim.food_listing.donor_id != current_user.id:
        flash('Access denied', 'danger')
        return redirect(url_for('donor_dashboard'))
    
    if claim.status != 'confirmed':
        flash('This claim cannot be verified. Status must be "confirmed".', 'warning')
        return redirect(url_for('donor_dashboard'))

    if (datetime.utcnow() - claim.created_at).total_seconds() > CLAIM_OTP_EXPIRY_SECONDS:
        flash('OTP expired. Please contact the NGO to arrange a new claim.', 'danger')
        return redirect(url_for('donor_dashboard'))
    
    if request.method == 'POST':
        otp_entered = request.form.get('otp')
        if otp_entered == claim.otp_code:
            claim.status = 'picked_up'
            claim.pickup_time = datetime.utcnow()
            claim.food_listing.status = 'picked_up'
            claim.otp_verified = True
            db.session.commit()
            
            # Create notification for NGO
            notification = Notification(
                user_id=claim.ngo_id,
                title='Pickup Verified!',
                message=f'Your pickup for {claim.food_listing.title} has been verified by the donor.',
                notification_type='claim_update'
            )
            db.session.add(notification)
            donor_notification = Notification(
                user_id=claim.food_listing.donor_id,
                title='Pickup Completed',
                message=f'Pickup completed for {claim.food_listing.title}.',
                notification_type='claim_update'
            )
            db.session.add(donor_notification)
            db.session.commit()
            
            flash('✅ OTP verified successfully! Pickup confirmed.', 'success')
            return redirect(url_for('donor_dashboard'))
        else:
            flash('❌ Invalid OTP. Please try again.', 'danger')
    
    time_elapsed = (datetime.utcnow() - claim.created_at).total_seconds()
    time_remaining = max(0, CLAIM_OTP_EXPIRY_SECONDS - time_elapsed)
    
    return render_template('donor/verify_otp.html', claim=claim, time_remaining=int(time_remaining))

@app.route('/admin')
@admin_login_required
def admin_dashboard():
    admin_id = session.get('admin_id')
    admin = db.session.get(Admin, admin_id)
    total_users = User.query.count()
    total_listings = FoodListing.query.count()
    total_claims = Claim.query.count()
    pending_ngos = User.query.filter_by(role='ngo', verified=False).count()
    review_count = Review.query.count()
    average_rating = db.session.query(db.func.avg(Review.rating)).scalar() or 0

    from_user = aliased(User)
    to_user = aliased(User)
    recent_review_rows = (
        db.session.query(
            Review.id,
            Review.rating,
            Review.comment,
            Review.created_at,
            Review.claim_id,
            from_user.username.label('from_username'),
            to_user.username.label('to_username'),
            FoodListing.title.label('listing_title')
        )
        .outerjoin(from_user, from_user.id == Review.from_user_id)
        .outerjoin(to_user, to_user.id == Review.to_user_id)
        .outerjoin(Claim, Claim.id == Review.claim_id)
        .outerjoin(FoodListing, FoodListing.id == Claim.food_listing_id)
        .order_by(Review.created_at.desc())
        .limit(5)
        .all()
    )

    recent_reviews = []
    for row in recent_review_rows:
        recent_reviews.append({
            'id': row.id,
            'rating': row.rating,
            'comment': row.comment,
            'created_at': row.created_at,
            'claim_id': row.claim_id,
            'listing_title': row.listing_title,
            'from_name': row.from_username or 'Anonymous',
            'to_name': row.to_username or 'Unknown user'
        })
    
    # Get monthly donation data for the chart
    from sqlalchemy import extract
    current_year = datetime.now().year
    monthly_donations = []
    for month in range(1, 4):  # Jan, Feb, Mar for the chart
        donations_count = FoodListing.query.filter(
            extract('year', FoodListing.created_at) == current_year,
            extract('month', FoodListing.created_at) == month
        ).count()
        monthly_donations.append(donations_count)
    
    return render_template('admin/dashboard.html',
                           admin=admin,
                           total_users=total_users,
                           total_listings=total_listings,
                           total_claims=total_claims,
                           pending_ngos=pending_ngos,
                           review_count=review_count,
                           average_rating=average_rating,
                           recent_reviews=recent_reviews,
                           monthly_donations=monthly_donations)

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
    cleanup_expired_listings()
    listings = FoodListing.query.order_by(FoodListing.created_at.desc()).all()
    return render_template('admin/listing.html', listings=listings)

@app.route('/admin/claims')
@admin_login_required
def admin_claims():
    claims = Claim.query.order_by(Claim.created_at.desc()).all()
    return render_template('admin/claims.html', claims=claims)

@app.route('/admin/notifications')
@admin_login_required
def admin_notifications():
    notifications = Notification.query.order_by(Notification.created_at.desc()).all()
    return render_template('admin/notifications.html', notifications=notifications)

@app.route('/admin/reports')
@admin_login_required
def admin_reports():
    # Get statistics for reports
    total_users = User.query.count()
    total_donors = User.query.filter_by(role='donor').count()
    total_ngos = User.query.filter_by(role='ngo').count()
    total_listings = FoodListing.query.count()
    total_claims = Claim.query.count()
    
    # Listing status breakdown
    available_listings = FoodListing.query.filter_by(status='available').count()
    claimed_listings = FoodListing.query.filter_by(status='claimed').count()
    picked_up_listings = FoodListing.query.filter_by(status='picked_up').count()
    
    # Claim status breakdown
    pending_claims = Claim.query.filter_by(status='pending').count()
    confirmed_claims = Claim.query.filter_by(status='confirmed').count()
    completed_claims = Claim.query.filter_by(status='picked_up').count()
    cancelled_claims = Claim.query.filter_by(status='cancelled').count()
    
    # Calculate total meals donated and claimed
    total_meals_donated = db.session.query(db.func.sum(FoodListing.quantity)).scalar() or 0
    total_meals_claimed = db.session.query(db.func.sum(Claim.people_served)).scalar() or 0
    
    # Get weekly meals served data (last 5 weeks)
    from datetime import timedelta
    from sqlalchemy import extract
    current_date = datetime.now().date()
    meals_weekly = []
    for i in range(4, -1, -1):
        week_start = current_date - timedelta(days=current_date.weekday() + (i * 7))
        week_end = week_start + timedelta(days=6)
        meals_count = db.session.query(db.func.sum(Claim.people_served)).filter(
            Claim.created_at >= week_start,
            Claim.created_at <= week_end,
            Claim.status == 'picked_up'
        ).scalar() or 0
        meals_weekly.append(int(meals_count))
    
    meals_week_1, meals_week_2, meals_week_3, meals_week_4, meals_week_5 = meals_weekly
    
    # Get monthly data for charts
    current_year = datetime.now().year
    
    monthly_donations = []
    monthly_claims = []
    for month in range(1, 13):
        donations_count = FoodListing.query.filter(
            extract('year', FoodListing.created_at) == current_year,
            extract('month', FoodListing.created_at) == month
        ).count()
        claims_count = Claim.query.filter(
            extract('year', Claim.created_at) == current_year,
            extract('month', Claim.created_at) == month
        ).count()
        
        monthly_donations.append(donations_count)
        monthly_claims.append(claims_count)
    
    # Get recent activity
    recent_listings = FoodListing.query.order_by(FoodListing.created_at.desc()).limit(5).all()
    recent_claims = Claim.query.order_by(Claim.created_at.desc()).limit(5).all()
    
    return render_template('admin/reports.html',
                           total_users=total_users,
                           total_donors=total_donors,
                           total_ngos=total_ngos,
                           total_listings=total_listings,
                           total_claims=total_claims,
                           available_listings=available_listings,
                           claimed_listings=claimed_listings,
                           picked_up_listings=picked_up_listings,
                           pending_claims=pending_claims,
                           confirmed_claims=confirmed_claims,
                           completed_claims=completed_claims,
                           cancelled_claims=cancelled_claims,
                           total_meals_donated=total_meals_donated,
                           total_meals_claimed=total_meals_claimed,
                           meals_week_1=meals_week_1,
                           meals_week_2=meals_week_2,
                           meals_week_3=meals_week_3,
                           meals_week_4=meals_week_4,
                           meals_week_5=meals_week_5,
                           monthly_donations=monthly_donations,
                           monthly_claims=monthly_claims,
                           recent_listings=recent_listings,
                           recent_claims=recent_claims,
                           current_year=current_year)

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

# =============== TTL AUTO-CANCELLATION ===============
CLAIM_OTP_EXPIRY_MINUTES = 2
CLAIM_OTP_EXPIRY_SECONDS = CLAIM_OTP_EXPIRY_MINUTES * 60

@app.route('/admin/cleanup_expired_claims')
@admin_login_required
def cleanup_expired_claims():
    """Auto-cancel claims older than 2 minutes without OTP verification"""
    expired_time = datetime.utcnow() - timedelta(minutes=CLAIM_OTP_EXPIRY_MINUTES)
    
    expired_claims = Claim.query.filter(
        Claim.created_at < expired_time,
        Claim.status.in_(['pending', 'confirmed']),
        Claim.otp_verified == False
    ).all()
    
    cancelled_count = 0
    for claim in expired_claims:
        claim.status = 'cancelled'
        claim.food_listing.status = 'available'
        
        # Notify NGO about auto-cancellation
        notification = Notification(
            user_id=claim.ngo_id,
            title='Claim Auto-Cancelled',
            message=f'Your claim for {claim.food_listing.title} was auto-cancelled due to timeout (2 minutes)',
            notification_type='claim_update'
        )
        db.session.add(notification)
        cancelled_count += 1
    
    db.session.commit()
    flash(f'Auto-cancelled {cancelled_count} expired claims.', 'info')
    return redirect(url_for('admin_dashboard'))

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
            'created_at': n.created_at.replace(tzinfo=timezone.utc).isoformat()
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
    available_cutoff = datetime.utcnow() - timedelta(hours=2)
    listings = FoodListing.query.filter_by(status='available')\
        .filter(FoodListing.created_at >= available_cutoff)\
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

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        subject = request.form.get('subject')
        message = request.form.get('message')
        newsletter = request.form.get('newsletter')
        
        # Here you would typically:
        # 1. Validate the form data
        # 2. Send an email notification
        # 3. Save to database
        # 4. Send confirmation to user
        
        flash('Thank you for your message! We will get back to you within 24 hours.', 'success')
        return redirect(url_for('contact'))
    
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
    # Expire listings that have been active for more than 2 hours.
    expiry_cutoff = datetime.utcnow() - timedelta(hours=2)
    expired_listings = FoodListing.query.filter(
        FoodListing.created_at < expiry_cutoff,
        FoodListing.status != 'expired'
    ).all()
    
    for listing in expired_listings:
        # Only update if not already expired
        if listing.status != 'expired':
            listing.status = 'expired'
            
            # Clean up old "Food Claimed!" notifications for expired listings
            old_notifications = Notification.query.filter_by(
                user_id=listing.donor_id,
                title='Food Claimed!',
                is_read=False
            ).all()
            
            for notification in old_notifications:
                if listing.title in notification.message:
                    # Mark old notification as read and create a new one
                    notification.is_read = True
                    
                    # Create corrected notification
                    corrected_notification = Notification(
                        user_id=listing.donor_id,
                        title='Food Listing Expired',
                        message=f'Your listing "{listing.title}" has expired and is no longer available.',
                        notification_type='claim_update'
                    )
                    db.session.add(corrected_notification)
                    break  # Only create one corrected notification per expired listing
        
    if expired_listings:
        db.session.commit()
        return len(expired_listings)
    return 0

def cleanup_expired_claims():
    """Auto-cancel claims older than 2 minutes without OTP verification"""
    expired_time = datetime.utcnow() - timedelta(minutes=CLAIM_OTP_EXPIRY_MINUTES)
    
    expired_claims = Claim.query.filter(
        Claim.created_at < expired_time,
        Claim.status.in_(['pending', 'confirmed']),
        Claim.otp_verified == False
    ).all()
    
    cancelled_count = 0
    for claim in expired_claims:
        claim.status = 'cancelled'
        claim.food_listing.status = 'available'
        
        # Notify NGO about auto-cancellation
        notification = Notification(
            user_id=claim.ngo_id,
            title='Claim Auto-Cancelled',
            message=f'Your claim for {claim.food_listing.title} was auto-cancelled due to timeout (2 minutes)',
            notification_type='claim_update'
        )
        db.session.add(notification)
        
        # Notify donor that the claim was cancelled and food is available again
        donor_notification = Notification(
            user_id=claim.food_listing.donor_id,
            title='Claim Cancelled - Food Available Again',
            message=f'The claim for {claim.food_listing.title} was cancelled. Your listing is available again.',
            notification_type='claim_update'
        )
        db.session.add(donor_notification)
        
        cancelled_count += 1
    
    if expired_claims:
        db.session.commit()
    return cancelled_count

# =============== INIT ===============
db_initialized = False

@app.before_request
def init_db():
    global db_initialized
    if not db_initialized:
        db.create_all()

        # Ensure the new verified column exists on existing databases
        inspector = inspect(db.engine)
        user_columns = [column['name'] for column in inspector.get_columns('user')]
        if 'verified' not in user_columns:
            db.session.execute(text('ALTER TABLE user ADD COLUMN verified BOOLEAN DEFAULT 0'))
            db.session.commit()

        # Create admin user if not exists
        admin_email = os.getenv('ADMIN_EMAIL', 'admin@example.com')
        admin = Admin.query.filter_by(email=admin_email).first()
        if not admin:
            admin = Admin(
                username='admin',
                email=admin_email,
                full_name='System Administrator',
                role='superadmin'
            )
            admin.set_password(os.getenv('ADMIN_PASSWORD', 'admin123'))
            db.session.add(admin)
            db.session.commit()
        db_initialized = True
    cleanup_expired_listings()
    cleanup_expired_claims()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)