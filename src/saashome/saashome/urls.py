"""
URL configuration for saashome project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from content.sitemaps import ArticleSitemap, FranchiseSitemap, LandingPageSitemap
from content.views import robots_txt_view

from .views import home_view


sitemaps = {
    "articles": ArticleSitemap,
    "landing_pages": LandingPageSitemap,
    "franchises": FranchiseSitemap,
}

urlpatterns = [
    path('', home_view, name='home'),
    path('auth/', include('allauth.urls')),
    path('accounts/', include('accounts.urls')),
    path('', include('analytics.urls')),
    path('', include('billing.urls')),
    path('', include('content.urls')),
    path('franchises/', include('franchises.urls')),
    path('leads/', include('leads.urls')),
    path('vendor/', include('vendor.urls')),
    path('visits/', include('visits.urls')),
    path('sitemap.xml', sitemap, {"sitemaps": sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', robots_txt_view, name='robots_txt'),
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
