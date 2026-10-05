# Migration & Deployment Guide: Render to Vercel with Neon PostgreSQL

This guide ensures **ZERO DATA LOSS** while migrating your Django course platform from **Render** to **Vercel** with a **Neon PostgreSQL** database.

---

## 🔒 Why Your Data Will NOT Be Lost
1. **Separation of Compute and Database**: 
   - Render and Vercel are **compute platforms** (they only run your Python web code).
   - Your data (users, courses, videos, lessons, purchases, and orders) lives in **Neon PostgreSQL**, which is a separate, managed cloud database.
   - When you switch from Render to Vercel, your Neon database remains completely untouched.
2. **Built-in Zero-Data-Loss Safety Tool**:
   - We created the management command: `python manage.py migrate_to_neon`
   - It allows you to create instant offline JSON backups, test database connections, verify record counts, and copy any remaining data from Render into Neon before switching.

---

## 📋 Step 1: Create an Offline Safety Backup (Zero-Risk Guarantee)

Run this command locally to create a timestamped offline JSON backup of all your current users, courses, modules, lessons, and orders:

```bash
python manage.py migrate_to_neon --export-backup
```

This will save `backup_clout_data.json` on your machine. Keep this file safe. Even if a server crashes, you can restore 100% of your data with:
```bash
python manage.py migrate_to_neon --restore-json backup_clout_data.json
```

---

## 🐘 Step 2: Get Your Neon PostgreSQL Connection String

1. Log into your [Neon Console](https://console.neon.tech/).
2. Select your project (e.g. `clout-db` or create one if you haven't yet).
3. Under **Dashboard**, find the **Connection Details** box.
4. Make sure to choose **Pooled connection** (this is vital for serverless Vercel deployments).
5. Copy the connection string. It looks like:
   ```text
   postgresql://username:password@ep-xyz-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   *(Note the `-pooler` in the host address and `sslmode=require`).*

---

## 🔄 Step 3: Populate Neon with Existing Data (If not already there)

### Case A: If your data is currently in your local `db.sqlite3`
To push all courses, lessons, users, and configurations from `db.sqlite3` to Neon:
1. In your local `.env` file, temporarily set your `DATABASE_URL` to your Neon PostgreSQL connection string:
   ```env
   DATABASE_URL=postgresql://username:password@ep-xyz-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
2. Run database migrations to prepare the Neon schema:
   ```bash
   python manage.py migrate
   ```
3. Run the zero-data-loss migration tool:
   ```bash
   python manage.py migrate_to_neon
   ```
4. Verify all tables in Neon:
   ```bash
   python manage.py migrate_to_neon --verify-only
   ```

### Case B: If your data is currently on a live Render PostgreSQL database
Run this single command locally, passing your Render PostgreSQL URL:
```bash
python manage.py migrate_to_neon --source-url "postgres://clout:password@dpg-...-a.oregon-postgres.render.com/clout_db"
```
This directly reads every record from Render and writes it to Neon with sequence alignment!

---

## 🚀 Step 4: Deploy to Vercel

### Method A: Deploy via GitHub (Recommended)
1. Push your latest code changes to your GitHub repository:
   ```bash
   git add .
   git commit -m "Configure Django for Vercel deployment with Neon PostgreSQL"
   git push origin main
   ```
2. Go to [Vercel Dashboard](https://vercel.com/dashboard) and click **"Add New..." > "Project"**.
3. Select your GitHub repository (`course` / `clout`).
4. In the Project Setup screen:
   - **Framework Preset**: Other / Django (Vercel automatically detects Django via `manage.py` and `clout/wsgi.py`).
   - **Root Directory**: `./` (leave default).

---

## 🔑 Step 5: Configure Environment Variables in Vercel

In the Vercel project setup (or under **Settings > Environment Variables**), add the following environment variables:

| Variable Name | Value | Purpose |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql://user:pass@ep-xyz-pooler...neon.tech/neondb?sslmode=require` | **Neon PostgreSQL pooled connection string** |
| `DEBUG` | `False` | Production mode |
| `DJANGO_SECRET_KEY` | *(A long random secret string)* | Security & session encryption |
| `ALLOWED_HOSTS` | `.vercel.app,clout.courses,www.clout.courses` | Allowed domains |
| `CSRF_TRUSTED_ORIGINS` | `https://*.vercel.app,https://clout.courses,https://www.clout.courses` | Prevents 403 CSRF errors |
| `RAZORPAY_KEY_ID` | `rzp_live_...` (or test key) | Payment processing |
| `RAZORPAY_KEY_SECRET` | *(Your Razorpay secret)* | Payment verification |
| `ADMIN_USERNAME` | `mubze` | Superuser username |
| `ADMIN_EMAIL` | `emirmubze@gmail.com` | Superuser email |
| `ADMIN_PASSWORD` | *(Your admin password)* | Superuser password |
| `RESEND_API_KEY` | *(Your Resend API key)* | Transactional emails |
| `RESEND_FROM_EMAIL` | `Clout <noreply@clout.courses>` | Sender email |
| `GEMINI_API_KEY` | *(Optional) AI Key* | AI features & subtitles |
| `GROQ_API_KEY` | *(Optional) Groq Key* | AI speech-to-text |

*(If you use Cloudflare R2 / AWS S3 for uploaded media):*
| Variable Name | Value |
| :--- | :--- |
| `USE_S3` | `True` |
| `AWS_ACCESS_KEY_ID` | *(Your access key)* |
| `AWS_SECRET_ACCESS_KEY` | *(Your secret key)* |
| `AWS_STORAGE_BUCKET_NAME` | `clout` |
| `AWS_S3_ENDPOINT_URL` | `https://<account_id>.r2.cloudflarestorage.com` |
| `AWS_S3_CUSTOM_DOMAIN` | *(Optional public domain, e.g. media.clout.courses)* |

Click **"Deploy"**!

---

## ✅ Step 6: Post-Deployment Verification

1. Once Vercel finishes the build, open your live deployment URL (e.g. `https://your-project.vercel.app`).
2. Log into `/admin/` or your customer login (`/login/`).
3. Verify that all courses, modules, lessons, and past users are present.
4. Test a purchase or test card checkout with Razorpay.
5. If using a custom domain (e.g., `clout.courses`), go to Vercel **Settings > Domains** and add your domain. DNS records will point to Vercel.

---

## 🛠️ Summary of What Was Fixed in the Codebase

1. **`clout/wsgi.py`**:
   - Added `app = application` so Vercel's serverless function discovery instantly recognizes the WSGI entrypoint.
2. **`clout/settings.py`**:
   - Added `.vercel.app`, `VERCEL_URL`, and `VERCEL_PROJECT_PRODUCTION_URL` to `ALLOWED_HOSTS`.
   - Added `https://*.vercel.app` to `CSRF_TRUSTED_ORIGINS` to avoid `403 CSRF verification failed` on all Vercel preview & production URLs.
   - Configured `DATABASE_URL` specifically for Neon PostgreSQL:
     - Detects `neon.tech` and enforces `sslmode=require`.
     - In serverless runtime (Vercel), defaults `DB_CONN_MAX_AGE` to `0` so connections are cleanly recycled back to Neon's pooler instead of leaking or dying.
   - Configured persistent data directory to safely fall back to `/tmp` in read-only serverless environments.
3. **`vercel.json`**:
   - Set serverless function `maxDuration: 60` seconds for `clout/wsgi.py` to prevent 504 gateway timeouts on complex database/payment/AI operations.
4. **`shop/management/commands/migrate_to_neon.py`**:
   - Created a complete backup, replication, verification, and sequence reset tool to ensure zero data loss.
