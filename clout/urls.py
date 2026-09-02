from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.http import QueryDict
from django.shortcuts import redirect
from django.urls import reverse

from shop.views import (
    serve_media_file,
    robots_txt,
    sitemap_xml,
    favicon_ico,
    favicon_png,
    apple_touch_icon,
    site_webmanifest,
)


def admin_login_redirect(request):
    query = QueryDict(mutable=True)
    next_url = request.GET.get("next")
    if next_url:
        query["next"] = next_url

    login_url = reverse("login")
    if query:
        login_url = f"{login_url}?{query.urlencode()}"

    return redirect(login_url)


urlpatterns = [
    path("robots.txt", robots_txt, name="root_robots_txt"),
    path("sitemap.xml", sitemap_xml, name="root_sitemap_xml"),
    path("favicon.ico", favicon_ico, name="root_favicon_ico"),
    path("favicon.png", favicon_png, name="root_favicon_png"),
    path("apple-touch-icon.png", apple_touch_icon, name="root_apple_touch_icon"),
    path("apple-touch-icon-precomposed.png", apple_touch_icon, name="root_apple_touch_icon_precomposed"),
    path("site.webmanifest", site_webmanifest, name="root_site_webmanifest"),
    path("manifest.json", site_webmanifest, name="root_manifest_json"),
    path("admin/login/", admin_login_redirect, name="admin_login_redirect"),
    path("admin/", admin.site.urls),
    path("media/<path:path>", serve_media_file, name="media_file"),
    path("", include("shop.urls")),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
