from django.db import models

class Patient(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)  # Make email optional
    phone = models.CharField(max_length=15)
    address = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

def __str__(self):
    return self.name


class Doctor(models.Model):
    name = models.CharField(max_length=100)
    specialization = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=15)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} - {self.specialization}"


class Appointment(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    doctor = models.ForeignKey('Doctor', on_delete=models.SET_NULL, null=True, blank=True)
    appointment_date = models.DateTimeField()

    def __str__(self):
        return f"{self.patient.name} - {self.appointment_date}"

