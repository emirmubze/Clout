from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from shop.views import serve_media_file

urlpatterns = [
    path("admin/", admin.site.urls),
    path("media/<path:path>", serve_media_file, name="media_file"),
    path("", include("shop.urls"))
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
