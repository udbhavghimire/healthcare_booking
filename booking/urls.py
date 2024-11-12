from django.urls import path
from . import views

urlpatterns = [
    path('book-appointment/', views.book_appointment, name='book_appointment'),
]