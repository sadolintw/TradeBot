from django.urls import path
# from rest_framework import serializers, viewsets, routers
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('webhook', views.webhook, name='webhook')
]