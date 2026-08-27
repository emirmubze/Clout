FLOW
1. Main index page = original uploaded Pasted text(1).txt.
2. Get the Course / Get More / pricing Buy Now CTAs open course_detail.html.
3. course_detail.html is the exact second uploaded code, with its Buy now connected to checkout.
4. Checkout Buy now opens Razorpay.
5. Successful payment opens payment_success.html.

Existing hover/CSS/JS from the uploaded index and course detail pages are preserved; only navigation connections were added.

PRODUCTION STORAGE

AUTHFORGE-STYLE JSON API

The project exposes Django-native authentication endpoints under /api/:

POST /api/users/ - register with name, email, and password
POST /api/sessions/ - log in and receive a bearer access token
POST /api/token/ - rotate the HttpOnly refresh token cookie
POST /api/logout/ - revoke the current refresh session
POST /api/logout/all/ - revoke all refresh sessions for the bearer user
GET /api/users/me/ - return the authenticated user
PUT /api/users/me/password/ - change password with currentPassword and newPassword
GET /api/admin-dashboard/ - staff-only example endpoint

Send access tokens as Authorization: Bearer <accessToken>. Configure
JWT_ACCESS_SECRET, JWT_ACCESS_EXPIRES_MINUTES, and JWT_REFRESH_EXPIRES_DAYS
in the environment. Login attempts are limited to five per IP in 15 minutes.

ADMIN LOGIN

Set these Render environment variables before deploying. They are used by
ensure_admin to create or repair the staff superuser:
ADMIN_USERNAME
ADMIN_EMAIL
ADMIN_PASSWORD

Render uses PostgreSQL through DATABASE_URL. The web service also needs an S3-compatible bucket for uploaded course videos, lesson thumbnails, profiles, and contact media because the Render filesystem is not durable across deploys.

Set these Render environment variables before deploying:
USE_S3=True
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_STORAGE_BUCKET_NAME
AWS_S3_REGION_NAME
AWS_S3_ENDPOINT_URL (optional for AWS; required by providers such as Cloudflare R2)
AWS_S3_CUSTOM_DOMAIN (optional)

The bucket must allow GET and byte-range requests (Range and Content-Range) from the live site. Keep the bucket private when possible; protected course video endpoints issue signed URLs.
