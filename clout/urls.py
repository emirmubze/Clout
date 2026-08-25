from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.http import QueryDict
from django.shortcuts import redirect
from django.urls import reverse

from shop.views import serve_media_file


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
    path("admin/login/", admin_login_redirect, name="admin_login_redirect"),
    path("admin/", admin.site.urls),
    path("media/<path:path>", serve_media_file, name="media_file"),
    path("", include("shop.urls"))
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
