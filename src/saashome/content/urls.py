from django.urls import path

from .views import article_detail_view, article_list_view, landing_page_detail_view


app_name = "content"

urlpatterns = [
    path("poradnik/", article_list_view, name="article_list"),
    path("poradnik/<slug:slug>/", article_detail_view, name="article_detail"),
    path("tematy/<slug:slug>/", landing_page_detail_view, name="landing_page_detail"),
]
