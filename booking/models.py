from django.db import models

class Patient(models.Model):
    name = models.CharField(max_length=100)
    
    def __str__(self):
        return self.name

class Appointment(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    
    class Meta:
        db_table = 'booking_appointment'
    
    def __str__(self):
        return f"{self.patient.name} - {self.appointment_date} {self.appointment_time}"



