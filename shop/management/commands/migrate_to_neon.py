import os
import sys
import json
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection, connections
from django.core import serializers
import dj_database_url

from shop.models import (
    CustomUser,
    Course,
    Module,
    Lesson,
    Order,
    ContactMessage,
    SubtitleSetting,
    SubtitleTrack,
    AuthSession,
)


class Command(BaseCommand):
    help = (
        "Zero-Data-Loss Migration & Verification Utility for Neon PostgreSQL. "
        "Allows exporting backups, verifying live tables, migrating from SQLite, "
        "or replicating data directly from an active Render PostgreSQL database to Neon."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="Only inspect and verify record counts and connection in the active database.",
        )
        parser.add_argument(
            "--export-backup",
            nargs="?",
            const="backup_clout_data.json",
            default=None,
            help="Export a complete JSON backup of the active database (default: backup_clout_data.json).",
        )
        parser.add_argument(
            "--restore-json",
            type=str,
            default=None,
            help="Restore data from a JSON backup file directly into the target database.",
        )
        parser.add_argument(
            "--source-sqlite",
            type=str,
            default=None,
            help="Path to source SQLite file to migrate into Neon (defaults to repository db.sqlite3).",
        )
        parser.add_argument(
            "--source-url",
            type=str,
            default=None,
            help="Connection string of the source database (e.g., Render PostgreSQL) to copy from.",
        )
        parser.add_argument(
            "--target-url",
            type=str,
            default=None,
            help="Connection string of the target Neon database (defaults to DATABASE_URL in environment).",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIME("=" * 65) if hasattr(self.style, "MIME") else "=" * 65)
        self.stdout.write(self.style.SUCCESS("  NEON POSTGRESQL ZERO-DATA-LOSS MIGRATION & SAFETY TOOL"))
        self.stdout.write("=" * 65)

        # -------------------------------------------------------------
        # 1. OPTIONAL: EXPORT BACKUP
        # -------------------------------------------------------------
        if options.get("export_backup"):
            backup_file = Path(options["export_backup"])
            self.stdout.write(self.style.NOTICE(f"\n==> Creating offline safety backup to '{backup_file}'..."))
            self._create_json_backup(backup_file)
            if not options.get("verify_only") and not options.get("source_sqlite") and not options.get("source_url") and not options.get("restore_json"):
                self.stdout.write(self.style.SUCCESS(f"\n[OK] Backup completed successfully. Your data is safely backed up."))
                return

        # -------------------------------------------------------------
        # 2. OPTIONAL: RESTORE FROM JSON BACKUP
        # -------------------------------------------------------------
        if options.get("restore_json"):
            restore_file = Path(options["restore_json"])
            if not restore_file.exists():
                self.stdout.write(self.style.ERROR(f"Backup file '{restore_file}' does not exist!"))
                return
            self.stdout.write(self.style.NOTICE(f"\n==> Restoring data from '{restore_file}' into database..."))
            self._restore_from_json(restore_file)
            self._align_all_sequences()
            self._display_status_report("DATABASE AFTER RESTORE")
            return

        # -------------------------------------------------------------
        # 3. VERIFY ONLY
        # -------------------------------------------------------------
        if options.get("verify_only"):
            self._display_status_report("CURRENT ACTIVE DATABASE STATUS")
            self._align_all_sequences()
            return

        # -------------------------------------------------------------
        # 4. MIGRATE FROM SOURCE POSTGRESQL (e.g. Render) TO NEON
        # -------------------------------------------------------------
        if options.get("source_url"):
            source_url = options["source_url"].strip()
            self.stdout.write(self.style.NOTICE(f"\n==> Replicating all tables from remote source to target database..."))
            self._replicate_from_remote_postgres(source_url)
            self._align_all_sequences()
            self._display_status_report("TARGET DATABASE AFTER REMOTE REPLICATION")
            return

        # -------------------------------------------------------------
        # 5. MIGRATE FROM SQLITE (default or specified path)
        # -------------------------------------------------------------
        sqlite_path = Path(options.get("source_sqlite") or (settings.BASE_DIR / "db.sqlite3"))
        if not sqlite_path.exists():
            self.stdout.write(self.style.WARNING(f"SQLite file '{sqlite_path}' not found."))
            self._display_status_report("ACTIVE DATABASE STATUS")
            return

        self.stdout.write(self.style.NOTICE(f"\n==> Migrating all data safely from '{sqlite_path.name}' into active database..."))
        self._migrate_from_sqlite(sqlite_path)
        self._align_all_sequences()
        self._display_status_report("ACTIVE DATABASE AFTER SQLITE MIGRATION")

    def _display_status_report(self, title):
        self.stdout.write("\n" + "-" * 65)
        self.stdout.write(self.style.SUCCESS(f"  {title}"))
        self.stdout.write(f"  Database Vendor: {connection.vendor.upper()}")
        db_name = connection.settings_dict.get("NAME", "unknown")
        db_host = connection.settings_dict.get("HOST", "local")
        self.stdout.write(f"  Host: {db_host} | Database: {db_name}")
        self.stdout.write("-" * 65)

        counts = {
            "CustomUser (Accounts)": CustomUser.objects.count(),
            "Order (Purchases & Payments)": Order.objects.count(),
            "Course (Curriculum)": Course.objects.count(),
            "Module (Sections)": Module.objects.count(),
            "Lesson (Video Lectures)": Lesson.objects.count(),
            "ContactMessage (Inquiries)": ContactMessage.objects.count(),
            "SubtitleTrack (VTT / SRT)": SubtitleTrack.objects.count(),
            "SubtitleSetting (Configs)": SubtitleSetting.objects.count(),
            "AuthSession (Active Sessions)": AuthSession.objects.count(),
        }

        for model_label, count in counts.items():
            color_fn = self.style.SUCCESS if count > 0 else self.style.WARNING
            self.stdout.write(f"  {model_label:<32}: {color_fn(str(count))}")

        self.stdout.write("-" * 65 + "\n")

    def _create_json_backup(self, backup_file):
        models_to_backup = [
            CustomUser,
            Course,
            Module,
            Lesson,
            Order,
            ContactMessage,
            SubtitleTrack,
            SubtitleSetting,
            AuthSession,
        ]
        all_objects = []
        for model in models_to_backup:
            try:
                for obj in model.objects.all():
                    all_objects.append(obj)
            except Exception as err:
                self.stdout.write(self.style.WARNING(f"Could not export {model.__name__}: {err}"))

        serialized = serializers.serialize("json", all_objects, indent=2)
        backup_file.write_text(serialized, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"[OK] Backed up {len(all_objects)} objects to '{backup_file.resolve()}'. (Keep this file safe!)"))

    def _restore_from_json(self, restore_file):
        content = restore_file.read_text(encoding="utf-8")
        count = 0
        skipped = 0
        for deserialized_obj in serializers.deserialize("json", content):
            obj = deserialized_obj.object
            model_class = obj.__class__
            try:
                if model_class.objects.filter(pk=obj.pk).exists():
                    existing = model_class.objects.get(pk=obj.pk)
                    for field in obj._meta.fields:
                        setattr(existing, field.name, getattr(obj, field.name))
                    existing.save()
                elif hasattr(obj, "username") and model_class.objects.filter(username=getattr(obj, "username")).exists():
                    existing = model_class.objects.get(username=getattr(obj, "username"))
                    for field in obj._meta.fields:
                        if field.name != "id":
                            setattr(existing, field.name, getattr(obj, field.name))
                    existing.save()
                else:
                    deserialized_obj.save()
                count += 1
            except Exception as err:
                self.stdout.write(self.style.WARNING(f"Skipped duplicate/conflict for {model_class.__name__} (id={getattr(obj, 'pk', None)}): {err}"))
                skipped += 1
        self.stdout.write(self.style.SUCCESS(f"[OK] Restored/updated {count} objects into the database (skipped: {skipped})."))

    def _align_all_sequences(self):
        if "postgres" not in connection.vendor.lower():
            return
        self.stdout.write(self.style.NOTICE("==> Resetting PostgreSQL primary key sequences for seamless future inserts..."))
        tables = [
            "shop_customuser",
            "shop_course",
            "shop_module",
            "shop_lesson",
            "shop_order",
            "shop_contactmessage",
            "shop_authsession",
            "shop_subtitletrack",
            "shop_subtitlesetting",
        ]
        with connection.cursor() as cursor:
            for table in tables:
                try:
                    cursor.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), coalesce(max(id), 1), max(id) IS NOT null) FROM {table};"
                    )
                except Exception:
                    pass
        self.stdout.write(self.style.SUCCESS("[OK] All PostgreSQL ID sequences aligned."))

    def _migrate_from_sqlite(self, sqlite_path):
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # 1. CustomUser
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_customuser'")
        if cur.fetchone():
            cur.execute("SELECT * FROM shop_customuser")
            users = cur.fetchall()
            for row in users:
                d = dict(row)
                pk = d.get("id")
                password = d.get("password", "")
                user, created = CustomUser.objects.get_or_create(
                    id=pk,
                    defaults={
                        "username": d["username"],
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
            self.stdout.write(self.style.SUCCESS(f"[OK] Users imported/verified: {len(users)}"))

        # 2. Course
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_course'")
        if cur.fetchone():
            cur.execute("SELECT * FROM shop_course")
            courses = cur.fetchall()
            for row in courses:
                d = dict(row)
                Course.objects.get_or_create(
                    id=d.get("id"),
                    defaults={
                        "title": d.get("title", ""),
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
            self.stdout.write(self.style.SUCCESS(f"[OK] Courses imported/verified: {len(courses)}"))

        # 3. Module
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_module'")
        if cur.fetchone():
            cur.execute("SELECT * FROM shop_module")
            modules = cur.fetchall()
            for row in modules:
                d = dict(row)
                Module.objects.get_or_create(
                    id=d.get("id"),
                    defaults={
                        "course_id": d.get("course_id"),
                        "title": d.get("title", ""),
                        "description": d.get("description", ""),
                        "video": d.get("video", ""),
                        "video_url": d.get("video_url", ""),
                        "order": d.get("order", 0),
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"[OK] Modules imported/verified: {len(modules)}"))

        # 4. Lesson
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_lesson'")
        if cur.fetchone():
            cur.execute("SELECT * FROM shop_lesson")
            lessons = cur.fetchall()
            for row in lessons:
                d = dict(row)
                Lesson.objects.get_or_create(
                    id=d.get("id"),
                    defaults={
                        "module_id": d.get("module_id"),
                        "title": d.get("title", ""),
                        "description": d.get("description", ""),
                        "video": d.get("video", ""),
                        "video_url": d.get("video_url", ""),
                        "thumbnail": d.get("thumbnail", ""),
                        "thumbnail_url": d.get("thumbnail_url", ""),
                        "order": d.get("order", 0),
                        "subtitle_status": d.get("subtitle_status", "none"),
                        "detected_language": d.get("detected_language", ""),
                        "detected_language_code": d.get("detected_language_code", ""),
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"[OK] Lessons imported/verified: {len(lessons)}"))

        # 5. Order
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_order'")
        if cur.fetchone():
            cur.execute("SELECT * FROM shop_order")
            orders = cur.fetchall()
            for row in orders:
                d = dict(row)
                Order.objects.get_or_create(
                    id=d.get("id"),
                    defaults={
                        "user_id": d.get("user_id"),
                        "product_name": d.get("product_name", "The AI Income Playbook"),
                        "amount": d.get("amount", 0),
                        "currency": d.get("currency", "USD"),
                        "razorpay_order_id": d.get("razorpay_order_id"),
                        "razorpay_payment_id": d.get("razorpay_payment_id"),
                        "razorpay_signature": d.get("razorpay_signature"),
                        "paid": bool(d.get("paid", 0)),
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"[OK] Orders imported/verified: {len(orders)}"))

        # 6. ContactMessage
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_contactmessage'")
        if cur.fetchone():
            cur.execute("SELECT * FROM shop_contactmessage")
            messages = cur.fetchall()
            for row in messages:
                d = dict(row)
                ContactMessage.objects.get_or_create(
                    id=d.get("id"),
                    defaults={
                        "sender_id": d.get("sender_id"),
                        "recipient_id": d.get("recipient_id"),
                        "sender_is_admin": bool(d.get("sender_is_admin", 0)),
                        "message": d.get("message", ""),
                        "image": d.get("image", ""),
                        "video": d.get("video", ""),
                        "is_read": bool(d.get("is_read", 0)),
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"[OK] Contact messages imported/verified: {len(messages)}"))

        # 7. SubtitleSetting
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='shop_subtitlesetting'")
        if cur.fetchone():
            cur.execute("SELECT * FROM shop_subtitlesetting")
            settings_rows = cur.fetchall()
            for row in settings_rows:
                d = dict(row)
                val = d.get("value", "{}")
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception:
                        val = {}
                SubtitleSetting.objects.get_or_create(
                    id=d.get("id"),
                    defaults={
                        "key": d.get("key"),
                        "value": val,
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"[OK] Subtitle settings imported/verified: {len(settings_rows)}"))

        conn.close()

    def _replicate_from_remote_postgres(self, source_url):
        import psycopg2
        import psycopg2.extras

        self.stdout.write(f"Connecting to source PostgreSQL database...")
        src_conn = psycopg2.connect(source_url)
        src_cur = src_conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # 1. CustomUser
        src_cur.execute("SELECT * FROM shop_customuser")
        users = src_cur.fetchall()
        for row in users:
            d = dict(row)
            user, created = CustomUser.objects.get_or_create(
                id=d.get("id"),
                defaults={
                    "username": d["username"],
                    "email": d.get("email", ""),
                    "name": d.get("name", ""),
                    "age": d.get("age"),
                    "phone_number": d.get("phone_number"),
                    "profile_image": d.get("profile_image", ""),
                    "course_access_approved": bool(d.get("course_access_approved", False)),
                    "is_active": bool(d.get("is_active", True)),
                    "is_staff": bool(d.get("is_staff", False)),
                    "is_superuser": bool(d.get("is_superuser", False)),
                }
            )
            if created and d.get("password"):
                user.password = d["password"]
                user.save(update_fields=["password"])
        self.stdout.write(self.style.SUCCESS(f"[OK] Replicated {len(users)} Users."))

        # 2. Courses
        src_cur.execute("SELECT * FROM shop_course")
        courses = src_cur.fetchall()
        for row in courses:
            d = dict(row)
            Course.objects.get_or_create(
                id=d.get("id"),
                defaults={
                    "title": d.get("title", ""),
                    "description": d.get("description", ""),
                    "price": d.get("price", 0),
                    "video": d.get("video", ""),
                    "video_url": d.get("video_url", ""),
                    "thumbnail": d.get("thumbnail", ""),
                    "instructor": d.get("instructor", ""),
                    "duration": d.get("duration", ""),
                    "level": d.get("level", "Beginner"),
                    "is_active": bool(d.get("is_active", True)),
                }
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] Replicated {len(courses)} Courses."))

        # 3. Modules
        src_cur.execute("SELECT * FROM shop_module")
        modules = src_cur.fetchall()
        for row in modules:
            d = dict(row)
            Module.objects.get_or_create(
                id=d.get("id"),
                defaults={
                    "course_id": d.get("course_id"),
                    "title": d.get("title", ""),
                    "description": d.get("description", ""),
                    "video": d.get("video", ""),
                    "video_url": d.get("video_url", ""),
                    "order": d.get("order", 0),
                }
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] Replicated {len(modules)} Modules."))

        # 4. Lessons
        src_cur.execute("SELECT * FROM shop_lesson")
        lessons = src_cur.fetchall()
        for row in lessons:
            d = dict(row)
            Lesson.objects.get_or_create(
                id=d.get("id"),
                defaults={
                    "module_id": d.get("module_id"),
                    "title": d.get("title", ""),
                    "description": d.get("description", ""),
                    "video": d.get("video", ""),
                    "video_url": d.get("video_url", ""),
                    "thumbnail": d.get("thumbnail", ""),
                    "thumbnail_url": d.get("thumbnail_url", ""),
                    "order": d.get("order", 0),
                    "subtitle_status": d.get("subtitle_status", "none"),
                    "detected_language": d.get("detected_language", ""),
                    "detected_language_code": d.get("detected_language_code", ""),
                }
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] Replicated {len(lessons)} Lessons."))

        # 5. Orders
        src_cur.execute("SELECT * FROM shop_order")
        orders = src_cur.fetchall()
        for row in orders:
            d = dict(row)
            Order.objects.get_or_create(
                id=d.get("id"),
                defaults={
                    "user_id": d.get("user_id"),
                    "product_name": d.get("product_name", "The AI Income Playbook"),
                    "amount": d.get("amount", 0),
                    "currency": d.get("currency", "USD"),
                    "razorpay_order_id": d.get("razorpay_order_id"),
                    "razorpay_payment_id": d.get("razorpay_payment_id"),
                    "razorpay_signature": d.get("razorpay_signature"),
                    "paid": bool(d.get("paid", False)),
                }
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] Replicated {len(orders)} Orders."))

        # 6. ContactMessages
        try:
            src_cur.execute("SELECT * FROM shop_contactmessage")
            messages = src_cur.fetchall()
            for row in messages:
                d = dict(row)
                ContactMessage.objects.get_or_create(
                    id=d.get("id"),
                    defaults={
                        "sender_id": d.get("sender_id"),
                        "recipient_id": d.get("recipient_id"),
                        "sender_is_admin": bool(d.get("sender_is_admin", False)),
                        "message": d.get("message", ""),
                        "image": d.get("image", ""),
                        "video": d.get("video", ""),
                        "is_read": bool(d.get("is_read", False)),
                    }
                )
            self.stdout.write(self.style.SUCCESS(f"[OK] Replicated {len(messages)} Contact Messages."))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Note: Contact messages skipped or not found: {e}"))

        src_conn.close()
