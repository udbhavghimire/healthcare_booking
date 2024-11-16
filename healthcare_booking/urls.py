from django.contrib import admin
from django.urls import path, include
from booking import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('book-appointment/', include('booking.urls')),
    path('check-db/', views.check_db, name='check_db'),
]