import os
import shutil
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection

from shop.models import (
    CustomUser,
    Course,
    Module,
    Lesson,
    Order,
)


class Command(BaseCommand):
    help = "Synchronize local media files and seed data to persistent production storage."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("==> Starting persistent data synchronization..."))

        # ---------------------------------------------------------
        # 1. SYNCHRONIZE MEDIA FILES TO PERSISTENT MEDIA STORAGE
        # ---------------------------------------------------------
        base_media = Path(settings.BASE_DIR) / "media"
        target_media = Path(settings.MEDIA_ROOT)

        try:
            target_media.mkdir(parents=True, exist_ok=True)
            for sub_dir in [
                "profiles",
                "course_videos",
                "course_thumbnails",
                "lesson_thumbnails",
                "contact_images",
                "contact_videos",
                "subtitles",
            ]:
                (target_media / sub_dir).mkdir(parents=True, exist_ok=True)
        except Exception as err:
            self.stdout.write(self.style.WARNING(f"Failed to create media directories: {err}"))

        if base_media.exists() and base_media.resolve() != target_media.resolve():
            synced_count = 0
            for root, _, files in os.walk(base_media):
                for file_name in files:
                    src_file = Path(root) / file_name
                    rel_path = src_file.relative_to(base_media)
                    dst_file = target_media / rel_path

                    if not dst_file.exists():
                        try:
                            dst_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, dst_file)
                            synced_count += 1
                        except Exception as copy_err:
                            self.stdout.write(self.style.WARNING(f"Could not copy {rel_path}: {copy_err}"))

            if synced_count > 0:
                self.stdout.write(self.style.SUCCESS(f"[OK] Synchronized {synced_count} media files to persistent storage."))
            else:
                self.stdout.write("[OK] Persistent media files are up to date.")
        else:
            self.stdout.write("[OK] Media storage directory verified.")

        # ---------------------------------------------------------
        # 2. AUTO-MIGRATE SQLITE TO POSTGRESQL IF POSTGRESQL IS EMPTY
        # ---------------------------------------------------------
        is_postgres = "postgres" in connection.vendor.lower()
        sqlite_source = Path(settings.BASE_DIR) / "db.sqlite3"

        if is_postgres and sqlite_source.exists():
            pg_user_count = CustomUser.objects.count()
            pg_course_count = Course.objects.count()

            if pg_course_count == 0 or pg_user_count == 0:
                self.stdout.write(self.style.NOTICE("==> PostgreSQL database missing initial seed data. Migrating seed data from SQLite safely..."))
                self._import_from_sqlite(sqlite_source)
            else:
                self.stdout.write(f"[OK] PostgreSQL database active with {pg_user_count} users, {pg_course_count} courses.")
        else:
            current_users = CustomUser.objects.count()
            current_courses = Course.objects.count()
            self.stdout.write(f"[OK] Database active with {current_users} users, {current_courses} courses.")

        self.stdout.write(self.style.SUCCESS("==> Persistent data synchronization complete."))

    def _import_from_sqlite(self, sqlite_path):
        try:
            conn = sqlite3.connect(sqlite_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 1. CustomUser
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_customuser'")
            if cursor.fetchone():
                cursor.execute("SELECT * FROM shop_customuser")
                users = cursor.fetchall()
                user_map = {}
                for row in users:
                    d = dict(row)
                    pk = d.pop("id", None)
                    password = d.pop("password", "")
                    user, created = CustomUser.objects.get_or_create(
                        username=d["username"],
                        defaults={
                            "email": d.get("email", ""),
                            "name": d.get("name", ""),
                            "age": d.get("age"),
                            "phone_number": d.get("phone_number"),
                            "profile_image": d.get("profile_image", ""),
                            "course_access_approved": bool(d.get("course_access_approved", 0)),
                            "is_active": bool(d.get("is_active", 1)),
                            "is_staff": bool(d.get("is_staff", 0)),
                            "is_superuser": bool(d.get("is_superuser", 0)),
                        }
                    )
                    if created and password:
                        user.password = password
                        user.save(update_fields=["password"])
                    if pk:
                        user_map[pk] = user

                self.stdout.write(self.style.SUCCESS(f"[OK] Migrated {len(user_map)} users to PostgreSQL."))
            else:
                user_map = {}

            # 2. Course
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_course'")
            if cursor.fetchone():
                cursor.execute("SELECT * FROM shop_course")
                courses = cursor.fetchall()
                course_map = {}
                for row in courses:
                    d = dict(row)
                    pk = d.pop("id", None)
                    course, _ = Course.objects.get_or_create(
                        title=d["title"],
                        defaults={
                            "description": d.get("description", ""),
                            "price": d.get("price", 0),
                            "video": d.get("video", ""),
                            "video_url": d.get("video_url", ""),
                            "thumbnail": d.get("thumbnail", ""),
                            "instructor": d.get("instructor", ""),
                            "duration": d.get("duration", ""),
                            "level": d.get("level", "Beginner"),
                            "is_active": bool(d.get("is_active", 1)),
                        }
                    )
                    if pk:
                        course_map[pk] = course

                self.stdout.write(self.style.SUCCESS(f"[OK] Migrated {len(course_map)} courses to PostgreSQL."))
            else:
                course_map = {}

            # 3. Module
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_module'")
            if cursor.fetchone():
                cursor.execute("SELECT * FROM shop_module")
                modules = cursor.fetchall()
                module_map = {}
                for row in modules:
                    d = dict(row)
                    pk = d.pop("id", None)
                    course_id = d.pop("course_id", None)
                    course = course_map.get(course_id) or Course.objects.filter(is_active=True).first() or Course.objects.first()
                    if not course:
                        continue

                    module, _ = Module.objects.get_or_create(
                        course=course,
                        title=d["title"],
                        defaults={
                            "description": d.get("description", ""),
                            "video": d.get("video", ""),
                            "video_url": d.get("video_url", ""),
                            "order": d.get("order", 0),
                        }
                    )
                    if pk:
                        module_map[pk] = module

                self.stdout.write(self.style.SUCCESS(f"[OK] Migrated {len(module_map)} modules to PostgreSQL."))
            else:
                module_map = {}

            # 4. Lesson
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_lesson'")
            if cursor.fetchone():
                cursor.execute("SELECT * FROM shop_lesson")
                lessons = cursor.fetchall()
                lesson_count = 0
                for row in lessons:
                    d = dict(row)
                    module_id = d.pop("module_id", None)
                    module = module_map.get(module_id) or Module.objects.first()
                    if not module:
                        continue

                    Lesson.objects.get_or_create(
                        module=module,
                        title=d["title"],
                        defaults={
                            "description": d.get("description", ""),
                            "video": d.get("video", ""),
                            "video_url": d.get("video_url", ""),
                            "thumbnail": d.get("thumbnail", ""),
                            "thumbnail_url": d.get("thumbnail_url", ""),
                            "order": d.get("order", 0),
                        }
                    )
                    lesson_count += 1

                self.stdout.write(self.style.SUCCESS(f"[OK] Migrated {lesson_count} lessons to PostgreSQL."))

            # 5. Order
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_order'")
            if cursor.fetchone():
                cursor.execute("SELECT * FROM shop_order")
                orders = cursor.fetchall()
                order_count = 0
                for row in orders:
                    d = dict(row)
                    user_id = d.pop("user_id", None)
                    user = user_map.get(user_id)

                    Order.objects.get_or_create(
                        razorpay_order_id=d["razorpay_order_id"],
                        defaults={
                            "user": user,
                            "product_name": d.get("product_name", "The AI Income Playbook"),
                            "amount": d.get("amount", 0),
                            "currency": d.get("currency", "USD"),
                            "razorpay_payment_id": d.get("razorpay_payment_id"),
                            "razorpay_signature": d.get("razorpay_signature"),
                            "paid": bool(d.get("paid", 0)),
                        }
                    )
                    order_count += 1

                self.stdout.write(self.style.SUCCESS(f"[OK] Migrated {order_count} orders to PostgreSQL."))

            conn.close()

            # Fix PostgreSQL auto-increment sequences if on PostgreSQL
            try:
                with connection.cursor() as pg_cur:
                    for table in [
                        "shop_customuser",
                        "shop_course",
                        "shop_module",
                        "shop_lesson",
                        "shop_order",
                        "shop_contactmessage",
                        "shop_authsession",
                    ]:
                        pg_cur.execute(
                            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), coalesce(max(id), 1), max(id) IS NOT null) FROM {table};"
                        )
            except Exception as seq_err:
                self.stdout.write(self.style.WARNING(f"Sequence reset warning: {seq_err}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error during SQLite to PostgreSQL migration: {e}"))
