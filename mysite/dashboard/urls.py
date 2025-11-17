from django.urls import path, include
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('auth/', include('social_django.urls', namespace='social')),
    path('', views.index, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('auth/login/google-oauth2/', views.auth_login, name='auth_login'),
    path('trading-status/', views.trading_status, name='trading_status'),
    path('execute-grid-v2/<str:symbol>/', views.execute_grid_v2_view, name='execute_grid_v2'),
] 