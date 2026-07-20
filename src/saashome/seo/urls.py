from django.urls import path

from .views import budget_detail_view, category_detail_view, how_it_works_view, methodology_view, model_detail_view


app_name = "seo"

urlpatterns = [
    path("franczyzy/k/<slug:slug>/", category_detail_view, name="category_detail"),
    path("franczyzy/budzet/<slug:slug>/", budget_detail_view, name="budget_detail"),
    path("franczyzy/model/<slug:slug>/", model_detail_view, name="model_detail"),
    path("metodologia/", methodology_view, name="methodology"),
    path("jak-to-dziala/", how_it_works_view, name="how_it_works"),
]
