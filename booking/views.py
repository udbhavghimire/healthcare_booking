import sys
import os
import datetime
import speech_recognition as sr
import spacy
from django.shortcuts import render, get_object_or_404
from .models import Patient, Doctor, Appointment
from django.http import JsonResponse
import re
from django.db.models import Q
from django.utils import timezone
from dateutil import parser
import time
from gtts import gTTS
from playsound import playsound
import json

def speak(text):
    """Generate speech using Google Text-to-Speech (gTTS) and play it using playsound."""
    tts = gTTS(text=text, lang='en')
    file_path = "speech.mp3"
    tts.save(file_path)
    playsound(file_path)
    os.remove(file_path)  # Remove the file after it has been played

# Load spaCy model for NER
nlp = spacy.load("en_core_web_sm")

def recognize_speech_with_timeout(timeout=20):
    """
    Recognize speech input with a specified timeout.
    
    :param timeout: The maximum number of seconds to listen for
    :return: The recognized text or None if no speech was recognized
    """
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening for appointment details...")
        try:
            audio = recognizer.listen(source, timeout=timeout)
            return recognizer.recognize_google(audio)
        except sr.WaitTimeoutError:
            print("No speech detected within the timeout period.")
            return None
        except sr.UnknownValueError:
            print("Speech was unintelligible.")
            return None
        except sr.RequestError as e:
            print(f"Could not request results; {e}")
            return None

def extract_entities(text):
    """Extract entities from the recognized text using spaCy NER."""
    doc = nlp(text)
    entities = {
        "patient_name": None,
        "appointment_date": None,
        "appointment_time": None
    }

    # Common patterns for name introduction - modified to capture just the name
    name_introduction_patterns = [
        r"my name is\s*([\w]+(?:\s+[\w]+)?)",  # Captures first and optional last name only
        r"i am\s*([\w]+(?:\s+[\w]+)?)",
        r"this is\s*([\w]+(?:\s+[\w]+)?)"
    ]

    # First try to find name using spaCy
    for ent in doc.ents:
        if ent.label_ == "PERSON" and not entities["patient_name"]:
            # Extract only the first two words if it's a name
            name_parts = ent.text.split()[:2]  # Limit to first two words
            entities["patient_name"] = " ".join(name_parts)
        elif ent.label_ == "DATE":
            entities["appointment_date"] = ent.text
        elif ent.label_ == "TIME":
            entities["appointment_time"] = ent.text

    # If no name was found by spaCy, try the patterns
    if not entities["patient_name"]:
        for pattern in name_introduction_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # Extract only the first two words if it's a name
                name_parts = match.group(1).strip().split()[:2]
                entities["patient_name"] = " ".join(name_parts)
                break

    # Clean up date extraction
    if not entities["appointment_date"]:
        date_keywords = ["today", "tomorrow", "next"]
        words = text.lower().split()
        for i, word in enumerate(words):
            if word in date_keywords:
                if word in ["today", "tomorrow"]:
                    entities["appointment_date"] = word
                elif word == "next" and i + 1 < len(words):
                    entities["appointment_date"] = f"next {words[i+1]}"
                break

    # Extract time if not found by spaCy
    if not entities["appointment_time"]:
        time_match = re.search(r'\d{1,2}:\d{2}\s*(?:am|pm|a\.m\.|p\.m\.)?', text, re.IGNORECASE)
        if time_match:
            entities["appointment_time"] = time_match.group()

    print(f"Extracted entities: {entities}")
    return entities

def parse_date(date_input):
    date_input = date_input.lower()
    try:
        if date_input == "today":
            return timezone.now().date()
        elif date_input == "tomorrow":
            return (timezone.now() + timezone.timedelta(days=1)).date()
        else:
            parsed_date = parser.parse(date_input, fuzzy=True).date()
            if parsed_date < timezone.now().date():
                raise ValueError("Appointments cannot be scheduled in the past. Please provide a future date.")
            return parsed_date
    except ValueError:
        raise ValueError("I couldn't understand the date. Could you please specify a valid future date?")

def parse_time(time_input):
    """Parse the time input and ensure it's a valid time."""
    try:
        parsed_time = parser.parse(time_input).time()
        return parsed_time
    except ValueError:
        raise ValueError("I couldn't understand the time format. Please use a format like '3:00 PM' or '15:00'.")

def ask_for_name():
    """Prompt the user to say their name or spell it."""
    speak("Please say or spell your name clearly.")
    voice_text = recognize_speech_with_timeout(timeout=20)
    return voice_text

def is_time_within_business_hours(time_obj):
    """Check if the time is within business hours (7:00 AM to 7:00 PM)."""
    business_start = datetime.time(7, 0)  # 7:00 AM
    business_end = datetime.time(19, 0)   # 7:00 PM
    return business_start <= time_obj <= business_end

def book_appointment(request):
    """Process voice command and book an appointment."""
    if request.method == "POST":
        # Initialize session storage for entities if not exists
        if 'booking_entities' not in request.session:
            request.session['booking_entities'] = {
                "patient_name": None,
                "appointment_date": None,
                "appointment_time": None
            }
        
        # Initial greeting
        if not request.POST and not request.body:
            # Clear previous session data when starting new booking
            request.session['booking_entities'] = {
                "patient_name": None,
                "appointment_date": None,
                "appointment_time": None
            }
            speak("Hello, how may I assist you today?")
            return JsonResponse({
                "status": "listening",
                "message": "Hello, how may I assist you today?"
            })
            
        # Check if this is a stop request
        if request.headers.get('X-Action') == 'stop':
            # Clear session data
            if 'booking_entities' in request.session:
                del request.session['booking_entities']
            speak("Booking session ended.")
            return JsonResponse({
                "status": "stopped",
                "message": "Booking session ended."
            })

        # Process voice input
        data = json.loads(request.body)
        voice_text = data.get('text')
        
        if not voice_text:
            speak("I'm sorry, I couldn't hear anything. Could you please repeat that?")
            return JsonResponse({
                "status": "error",
                "message": "I'm sorry, I couldn't hear anything. Could you please repeat that?"
            })
        
        # Get current entities from session
        current_entities = request.session['booking_entities']
        
        # Extract new entities
        new_entities = extract_entities(voice_text)
        
        # Update session with any new information
        if new_entities['patient_name']:
            current_entities['patient_name'] = new_entities['patient_name']
        if new_entities['appointment_date']:
            current_entities['appointment_date'] = new_entities['appointment_date']
        if new_entities['appointment_time']:
            current_entities['appointment_time'] = new_entities['appointment_time']
        
        # Save updated entities back to session
        request.session['booking_entities'] = current_entities
        request.session.modified = True
        
        # Process the booking conversation
        if not current_entities["patient_name"]:
            speak("Could you please tell me your name?")
            return JsonResponse({
                "status": "listening",
                "message": "Could you please tell me your name?"
            })
            
        if not current_entities["appointment_date"]:
            speak("What date would you like to book the appointment for?")
            return JsonResponse({
                "status": "listening",
                "message": "What date would you like to book the appointment for?"
            })
            
        if not current_entities["appointment_time"]:
            speak("What time would you like to book the appointment?")
            return JsonResponse({
                "status": "listening",
                "message": "What time would you like to book the appointment?"
            })
            
        try:
            # Parse date and time
            appointment_date = parse_date(current_entities["appointment_date"])
            appointment_time = parse_time(current_entities["appointment_time"])
            
            # Check if time is within business hours
            if not is_time_within_business_hours(appointment_time):
                speak("I apologize, but appointments are only available between 7 AM and 7 PM. Please choose a different time.")
                return JsonResponse({
                    "status": "listening",
                    "message": "I apologize, but appointments are only available between 7 AM and 7 PM. Please choose a different time."
                })
            
            # Create appointment datetime
            appointment_datetime = timezone.make_aware(
                datetime.datetime.combine(appointment_date, appointment_time)
            )
            
            # Check if time slot is available
            if not is_time_available(appointment_datetime):
                speak("I apologize, but that time slot is already booked. Please choose a different time.")
                return JsonResponse({
                    "status": "listening",
                    "message": "I apologize, but that time slot is already booked. Please choose a different time."
                })
            
            # Create the appointment
            patient, created = Patient.objects.get_or_create(name=current_entities["patient_name"])
            appointment = Appointment.objects.create(
                patient=patient,
                appointment_date=appointment_datetime
            )
            
            # Clear session data after successful booking
            del request.session['booking_entities']
            
            confirmation_message = f"Great! I've booked your appointment for {appointment_date.strftime('%A, %B %d')} at {appointment_time.strftime('%I:%M %p')}."
            speak(confirmation_message)
            return JsonResponse({
                "status": "completed",
                "message": confirmation_message
            })
            
        except ValueError as e:
            speak(str(e))
            return JsonResponse({
                "status": "listening",
                "message": str(e)
            })

    return render(request, "book_appointment.html")

def is_time_available(appointment_datetime):
    """
    Check if the appointment time is available by verifying if the time slot is already booked.
    """
    appointment_start = appointment_datetime
    appointment_end = appointment_start + timezone.timedelta(minutes=35)  # Assuming appointments last 30 minutes

    # Check if there's any overlapping appointment
    existing_appointment = Appointment.objects.filter(
        Q(appointment_date__lt=appointment_end) &
        Q(appointment_date__gt=appointment_start - timezone.timedelta(minutes=35))
    ).exists()

    return not existing_appointment  # Return True if no overlapping appointments are found