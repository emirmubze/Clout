# Django Course Clout - Complete Setup Guide

## Project Overview
This is a Django-based course selling platform that integrates with Razorpay for payment processing. The project includes user authentication, course details, checkout, and payment verification.

---

## 🏗️ Project Architecture

### Models (`shop/models.py`)

#### CustomUser
```python
- Extends Django's AbstractUser
- Fields: name, age, phone_number, email, username, password
- Purpose: Custom user model for authentication
```

#### Order
```python
- user: ForeignKey to CustomUser
- product_name: CharField (default: "The AI Income Playbook")
- amount: DecimalField (price)
- currency: CharField (default: "USD")
- razorpay_order_id: CharField (unique)
- razorpay_payment_id: CharField (from Razorpay)
- razorpay_signature: CharField (from Razorpay)
- paid: BooleanField (tracks payment status)
- created_at: DateTimeField (auto timestamp)
```

---

## 📋 URL Routing (`shop/urls.py`)

| Route | View | Template | Auth Required |
|-------|------|----------|---------------|
| `/` | index | index.html | No |
| `/course/` | course_detail | course_detail.html | No |
| `/login/` | LoginView | signin.html | No |
| `/register/` | register_view | signup.html | No |
| `/logout/` | logout_view | - | Yes |
| `/checkout/` | checkout | checkout.html | Yes |
| `/create-order/` | create_order | - (JSON) | Yes |
| `/verify-payment/` | verify_payment | - (JSON) | Yes |
| `/payment-success/` | payment_success | payment_success.html | Yes |

---

## 🔐 Authentication Flow

### 1. Registration (`/register/`)
```
User fills form → RegistrationForm validates → CustomUser created → Auto-login → Redirect to index
```

**Form Fields:**
- name (required)
- email (required, unique)
- phone_number (optional)
- age (optional)
- password1 (required)
- password2 (required, must match)

### 2. Login (`/login/`)
```
User submits credentials → Django LoginView authenticates → Redirect to LOGIN_REDIRECT_URL (index)
```

### 3. Logout (`/logout/`)
```
User clicks logout → Session cleared → Redirect to LOGOUT_REDIRECT_URL (index)
```

---

## 💳 Payment Flow

### 1. Checkout Page (`/checkout/`)
- Accessible only to logged-in users
- Displays: Product name, amount, tax, total
- User clicks "Buy now" button

### 2. Create Razorpay Order (`/create-order/` - POST)
```javascript
Request → Validate API keys → Create order in Razorpay → Save Order to DB → Return order details
```
**Response:**
```json
{
  "success": true,
  "key": "razorpay_key_id",
  "order_id": "order_xxxxx",
  "amount": 1882,
  "currency": "USD"
}
```

### 3. Razorpay Payment Modal
- User enters payment details
- Razorpay processes payment
- Returns payment_id and signature

### 4. Verify Payment (`/verify-payment/` - POST)
```
Payment data → Verify signature with Razorpay → Update Order (paid=True) → Return success
```
**Response:**
```json
{
  "success": true,
  "redirect_url": "/payment-success/"
}
```

### 5. Payment Success (`/payment-success/`)
- Confirmation page shown to user
- Only accessible after successful payment verification

---

## ⚙️ Settings Configuration (`clout/settings.py`)

### Database
- SQLite3 (default development)
- Location: `db.sqlite3`

### Authentication
```python
AUTH_USER_MODEL = "shop.CustomUser"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "index"
LOGOUT_REDIRECT_URL = "index"
```

### Razorpay Integration
```python
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
```
*Note: Add these to `.env` file*

### Static Files
```python
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "shop" / "static"]
```

---

## 📝 Forms (`shop/forms.py`)

### RegistrationForm
Extends `UserCreationForm` with additional fields:
- name
- email
- phone_number
- age

Custom `save()` method to populate all fields correctly.

---

## 📄 HTML Templates

### Templates Directory: `shop/templates/shop/`

1. **index.html** - Home page
2. **course_detail.html** - Course information and purchase card
3. **signin.html** - Login form
4. **signup.html** - Registration form
5. **checkout.html** - Checkout details and Razorpay integration
6. **payment_success.html** - Order confirmation
7. **forgot-password.html** - Password recovery
8. **reset-password.html** - Reset password form
9. **password-reset-success.html** - Reset confirmation
10. **reset-link-sent.html** - Link sent confirmation

---

## 🔄 Database Migrations

### Applied Migrations
```
0001_initial.py - Initial CustomUser and Order models
0002_order_user_alter_customuser_age_and_more.py - Added user ForeignKey to Order
```

### To Apply Migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

---

## 👨‍💼 Admin Panel

### Access: `/admin/`

#### CustomUser Admin
- List view: username, email, name, phone_number, is_staff
- Filters: age
- Custom fieldsets for easy management

#### Order Admin
- List view: id, user, product_name, amount, paid, created_at
- Filters: paid status, created date, currency
- Search: by user email, razorpay_order_id, product name
- Read-only fields: Razorpay details and timestamps

---

## 🚀 Running the Application

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Create Superuser (for admin access)
```bash
python manage.py createsuperuser
```

### Run Development Server
```bash
python manage.py runserver
```

### Access the Application
- Home: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/

---

## 📦 Dependencies

```
Django>=5.0,<6.0
razorpay
python-dotenv
```

---

## 🔑 Environment Variables (.env)

```
DJANGO_SECRET_KEY=your-secret-key-here
RAZORPAY_KEY_ID=your-razorpay-key-id
RAZORPAY_KEY_SECRET=your-razorpay-secret-key
```

---

## 🧪 Testing Razorpay Integration

Use Razorpay test credentials with test card:
- Card: 4111 1111 1111 1111
- Expiry: Any future date
- CVV: Any 3 digits

---

## 📱 Responsive Design

All templates include media queries for:
- Desktop (>900px)
- Tablet (800px-900px)
- Mobile (<800px)
- Small Mobile (<600px)

---

## 🔒 Security Features

- CSRF protection enabled
- Login required for checkout and payment
- Payment verification via Razorpay signature
- User can only verify their own orders
- Secure password hashing

---

## 📊 Data Flow Diagram

```
User Registration → CustomUser Created
                        ↓
                    User Login
                        ↓
                   Index Page
                        ↓
                  Course Detail
                        ↓
                    Checkout
                        ↓
                Create Razorpay Order (Order created in DB)
                        ↓
              Razorpay Payment Modal
                        ↓
              Verify Payment Signature
                        ↓
                  Payment Success
```

---

## ✅ All Pages Connected

- ✅ Home page (`index`) links to course detail
- ✅ Course detail page (`course_detail`) links to checkout
- ✅ Login page (`signin`) redirects to index after auth
- ✅ Register page (`signup`) with auto-login redirect
- ✅ Checkout page requires authentication
- ✅ Payment flow integrated with Razorpay
- ✅ Success page confirms purchase
- ✅ Navbar links to home and course pages
- ✅ User authentication flow complete
- ✅ Admin panel for managing users and orders

---

## 🐛 Troubleshooting

### Port already in use
```bash
python manage.py runserver 0.0.0.0:8001
```

### Database errors
```bash
python manage.py flush  # Clear database
python manage.py migrate  # Reapply migrations
```

### Static files not loading
```bash
python manage.py collectstatic
```

---

**Setup Complete!** All pages are properly connected and the application is ready for development. 🎉
