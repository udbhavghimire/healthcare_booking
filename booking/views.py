import datetime
import json
import re
import spacy
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from dateutil import parser
from .models import Patient, Appointment
from django.views.decorators.csrf import ensure_csrf_cookie
from gtts import gTTS
import base64
import tempfile
import os
import traceback
import logging
import sqlite3
from django.contrib import messages

# Load spaCy model for NER
nlp = spacy.load("en_core_web_sm")

logger = logging.getLogger(__name__)

def extract_entities(text):
    """Extract entities from the recognized text using spaCy NER."""
    doc = nlp(text)
    entities = {
        "patient_name": None,
        "appointment_date": None,
        "appointment_time": None
    }

    # Extract person name, date, and time entities
    for ent in doc.ents:
        if ent.label_ == "PERSON" and not entities["patient_name"]:
            entities["patient_name"] = ent.text
        elif ent.label_ == "DATE":
            entities["appointment_date"] = ent.text
        elif ent.label_ == "TIME":
            entities["appointment_time"] = ent.text

    # Simple regex for alternative name introduction patterns
    if not entities["patient_name"]:
        name_match = re.search(r"my name is\s*([\w\s]+)", text, re.IGNORECASE)
        if name_match:
            entities["patient_name"] = name_match.group(1).strip()

    print(f"Extracted entities: {entities}")  # Debug log
    return entities

def parse_date(date_input):
    """Parse date from user input."""
    try:
        text = date_input.lower().strip()
        today = timezone.now().date()
        
        # Handle "tomorrow"
        if "tomorrow" in text:
            return today + timezone.timedelta(days=1)
            
        # Handle "today"
        if "today" in text:
            return today
            
        # Handle "next" + day of week
        days = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6
        }
        
        for day, day_num in days.items():
            if f"next {day}" in text:
                current_day = today.weekday()
                days_ahead = day_num - current_day
                if days_ahead <= 0:  # Target day has passed this week
                    days_ahead += 7  # Move to next week
                return today + timezone.timedelta(days=days_ahead)
                
        # Handle day of week without "next"
        for day, day_num in days.items():
            if day in text and "next" not in text:
                current_day = today.weekday()
                days_ahead = day_num - current_day
                if days_ahead <= 0:  # Target day has passed this week
                    days_ahead += 7  # Move to next week
                return today + timezone.timedelta(days=days_ahead)
        
        # Try parsing as a specific date
        parsed_date = parser.parse(text, fuzzy=True).date()
        if parsed_date < today:
            raise ValueError("Cannot book appointments for past dates")
        return parsed_date
        
    except Exception as e:
        print(f"Date parsing error: {e}")
        raise ValueError("Please provide a valid future date")

def parse_time(time_input):
    """Parse time from user input."""
    try:
        return parser.parse(time_input).time()
    except ValueError:
        raise ValueError("Please provide a time in '3:00 PM' or '15:00' format.")

def get_available_slots(appointment_date, period=None):
    """Get available time slots for a given date and period"""
    print(f"Getting slots for date: {appointment_date}, period: {period}")
    
    # Define time ranges
    periods = {
        'morning': (7, 12),    # 7 AM to 12 PM
        'afternoon': (12, 17), # 12 PM to 5 PM
        'evening': (17, 19)    # 5 PM to 7 PM
    }
    
    try:
        # Get all booked appointments for the date
        booked_appointments = Appointment.objects.filter(
            appointment_date=appointment_date
        )
        print(f"Found {booked_appointments.count()} booked appointments")
        
        # Get booked hours
        booked_hours = {appt.appointment_time.hour for appt in booked_appointments}
        print(f"Booked hours: {booked_hours}")
        
        def format_time(hour):
            if hour == 12:
                return f"12:00 PM"
            elif hour > 12:
                return f"{hour-12:02d}:00 PM"
            else:
                return f"{hour:02d}:00 AM"
        
        if period:
            if period not in periods:
                raise ValueError(f"Invalid period: {period}")
            
            start_hour, end_hour = periods[period]
            available_slots = [
                format_time(hour)
                for hour in range(start_hour, end_hour)
                if hour not in booked_hours
            ]
            print(f"Available slots for {period}: {available_slots}")
            return available_slots
        else:
            all_slots = {
                'morning': [format_time(hour) for hour in range(7, 12) if hour not in booked_hours],
                'afternoon': [format_time(hour) for hour in range(12, 17) if hour not in booked_hours],
                'evening': [format_time(hour) for hour in range(17, 19) if hour not in booked_hours]
            }
            print(f"All available slots: {all_slots}")
            return all_slots
            
    except Exception as e:
        print(f"Error in get_available_slots: {str(e)}")
        print(traceback.format_exc())
        raise

def format_time_slots(slots):
    """Categorize slots into morning, afternoon, and evening."""
    morning_slots = [slot for slot in slots if slot.time() < datetime.time(12, 0)]
    afternoon_slots = [slot for slot in slots if datetime.time(12, 0) <= slot.time() < datetime.time(17, 0)]
    evening_slots = [slot for slot in slots if slot.time() >= datetime.time(17, 0)]
    print(f"Formatted slots - Morning: {morning_slots}, Afternoon: {afternoon_slots}, Evening: {evening_slots}")  # Debug log
    return {
        "morning": morning_slots,
        "afternoon": afternoon_slots,
        "evening": evening_slots
    }

def generate_speech(text):
    """Generate speech using gTTS and return as base64 audio data"""
    try:
        print(f"Generating speech for: {text}")  # Debug log
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as fp:
            tts = gTTS(text=text, lang='en')
            tts.save(fp.name)
            with open(fp.name, 'rb') as audio_file:
                audio_data = base64.b64encode(audio_file.read()).decode('utf-8')
            os.unlink(fp.name)
            print(f"Generated audio data length: {len(audio_data)}")  # Debug log
            return audio_data
    except Exception as e:
        print(f"Error generating speech: {e}")
        print(traceback.format_exc())  # Full error trace
        return None

@ensure_csrf_cookie
def book_appointment(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode('utf-8'))
            print(f"Received data: {data}")
            
            # Initialize session if needed
            if 'booking_entities' not in request.session:
                request.session['booking_entities'] = {
                    "patient_name": None,
                    "appointment_date": None,
                    "appointment_time": None,
                    "conversation_state": "initial"
                }
            
            # Handle ping request
            if data.get('type') == 'ping':
                return JsonResponse({
                    "status": "success",
                    "message": "Server is alive"
                })
            
            # Handle greeting
            if data.get('type') == 'greeting':
                # Reset session
                request.session['booking_entities'] = {
                    "patient_name": None,
                    "appointment_date": None,
                    "appointment_time": None,
                    "conversation_state": "initial"
                }
                message = "Hello, how may I assist you today?"
                return JsonResponse({
                    "status": "success",
                    "message": message,
                    "audio": generate_speech(message)
                })
            
            # Handle voice input
            if data.get('type') == 'voice_input':
                voice_text = data.get('text', '').strip()
                current_entities = request.session.get('booking_entities', {})
                
                print("DEBUG - Session data:", current_entities)
                print("DEBUG - Current state:", current_entities.get('conversation_state'))
                
                if not current_entities or 'conversation_state' not in current_entities:
                    print("DEBUG - Session reset required")
                    current_entities = {
                        "patient_name": None,
                        "appointment_date": None,
                        "appointment_time": None,
                        "conversation_state": "initial"
                    }
                    request.session['booking_entities'] = current_entities
                
                print(f"Current state: {current_entities['conversation_state']}")
                print(f"Received voice input: {voice_text}")

                # Initial state
                if current_entities["conversation_state"] == "initial":
                    if "book" in voice_text and "appointment" in voice_text:
                        current_entities["conversation_state"] = "waiting_for_name"
                        request.session.modified = True
                        message = "Could you please tell me your name?"
                        return JsonResponse({
                            "status": "success",
                            "message": message,
                            "audio": generate_speech(message)
                        })

                # Waiting for name
                elif current_entities["conversation_state"] == "waiting_for_name":
                    # Enhanced name pattern matching
                    name_patterns = [
                        r"my name is\s*([\w\s]+)",
                        r"i am\s*([\w\s]+)",
                        r"this is\s*([\w\s]+)",
                        r"(?:call me|i'm)\s*([\w\s]+)"
                    ]
                    
                    name_match = None
                    for pattern in name_patterns:
                        match = re.search(pattern, voice_text, re.IGNORECASE)
                        if match:
                            name_match = match
                            break
                    
                    if name_match:
                        name = name_match.group(1).strip()
                        # Remove common fillers
                        name = re.sub(r'\b(um|uh|er|ah)\b', '', name, flags=re.IGNORECASE).strip()
                        
                        if len(name) > 0:  # Ensure we have a non-empty name
                            current_entities["patient_name"] = name
                            current_entities["conversation_state"] = "waiting_for_date"
                            request.session.modified = True
                            message = f"Thank you, {name}. What date would you like to book the appointment for?"
                            return JsonResponse({
                                "status": "success",
                                "message": message,
                                "audio": generate_speech(message)
                            })
                    
                    message = "I didn't catch your name. Could you please say it again, starting with 'My name is' or 'I am'?"
                    return JsonResponse({
                        "status": "success",
                        "message": message,
                        "audio": generate_speech(message)
                    })

                # Waiting for date
                elif current_entities["conversation_state"] == "waiting_for_date":
                    try:
                        # Try to parse the date from voice input
                        try:
                            date = parse_date(voice_text)
                            current_entities["appointment_date"] = date.isoformat()
                            current_entities["conversation_state"] = "waiting_for_time"
                            request.session.modified = True
                            message = "What time would you prefer? We have slots available in the morning (7 AM to 12 PM), afternoon (12 PM to 5 PM), and evening (5 PM to 7 PM)"
                            return JsonResponse({
                                "status": "success",
                                "message": message,
                                "audio": generate_speech(message)
                            })
                        except ValueError as e:
                            message = str(e)
                            if "past dates" in message:
                                message = "Sorry, you cannot book appointments for past dates. Please choose a future date."
                            else:
                                message = "I couldn't understand the date. You can say things like:\n- tomorrow\n- next Monday\n- next Friday\n- Saturday"
                            return JsonResponse({
                                "status": "success",
                                "message": message,
                                "audio": generate_speech(message)
                            })
                            
                    except Exception as e:
                        print(f"Error processing date: {e}")
                        message = "I couldn't understand the date. You can say things like:\n- tomorrow\n- next Monday\n- next Friday\n- Saturday"
                        return JsonResponse({
                            "status": "success",
                            "message": message,
                            "audio": generate_speech(message)
                        })

                # Waiting for time
                elif current_entities["conversation_state"] == "waiting_for_time":
                    try:
                        date = datetime.date.fromisoformat(current_entities["appointment_date"])
                        print(f"Processing time for date: {date}")
                        
                        # Handle booking requests
                        time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)', voice_text, re.IGNORECASE)
                        if time_match:
                            hour = int(time_match.group(1))
                            minutes = int(time_match.group(2)) if time_match.group(2) else 0
                            period = time_match.group(3).lower().replace('.', '')
                            
                            # Convert to 24-hour format
                            if period in ['pm', 'p.m'] and hour != 12:
                                hour += 12
                            elif period in ['am', 'a.m'] and hour == 12:
                                hour = 0
                                
                            appointment_time = datetime.time(hour, minutes)
                            
                            # Check if slot is available
                            if Appointment.objects.filter(
                                appointment_date=date,
                                appointment_time=appointment_time
                            ).exists():
                                message = "Sorry, that time slot is already booked. Would you like to see available slots?"
                            elif 7 <= hour < 19:  # Between 7 AM and 7 PM
                                # Store the time temporarily and ask for confirmation
                                current_entities["appointment_time"] = appointment_time.strftime('%H:%M')
                                current_entities["conversation_state"] = "waiting_for_confirmation"
                                request.session.modified = True
                                
                                message = (f"Please confirm your appointment details:\n"
                                          f"Name: {current_entities['patient_name']}\n"
                                          f"Date: {date.strftime('%A, %B %d, %Y')}\n"
                                          f"Time: {appointment_time.strftime('%I:%M %p')}\n\n"
                                          f"Would you like to confirm this booking? Please say 'yes, book it' to confirm or 'no' to cancel.")
                            else:
                                message = "Sorry, that time is outside our operating hours (7 AM to 7 PM). Would you like to see available slots?"
                                
                        # Handle requests for available slots
                        elif any(word in voice_text.lower() for word in ["available", "what", "show", "slots", "free", "time"]):
                            try:
                                if "morning" in voice_text.lower():
                                    slots = get_available_slots(date, 'morning')
                                    message = f"Available morning slots are: {', '.join(slots)}" if slots else "Sorry, no morning slots available."
                                elif "afternoon" in voice_text.lower():
                                    slots = get_available_slots(date, 'afternoon')
                                    message = f"Available afternoon slots are: {', '.join(slots)}" if slots else "Sorry, no afternoon slots available."
                                elif "evening" in voice_text.lower():
                                    slots = get_available_slots(date, 'evening')
                                    message = f"Available evening slots are: {', '.join(slots)}" if slots else "Sorry, no evening slots available."
                                else:
                                    all_slots = get_available_slots(date)
                                    message = "Available slots are:\n"
                                    if all_slots['morning']: message += f"Morning: {', '.join(all_slots['morning'])}\n"
                                    if all_slots['afternoon']: message += f"Afternoon: {', '.join(all_slots['afternoon'])}\n"
                                    if all_slots['evening']: message += f"Evening: {', '.join(all_slots['evening'])}"
                                    if not any(all_slots.values()): message = "Sorry, no slots available for this date."
                            except Exception as e:
                                print(f"Error getting slots: {str(e)}")
                                print(traceback.format_exc())
                                message = "Sorry, there was an error checking available slots. Please try again."
                        else:
                            message = "I couldn't understand that. Please specify a time (like '9:00 AM') or ask to see available slots."
                            
                        return JsonResponse({
                            "status": "success",
                            "message": message,
                            "audio": generate_speech(message)
                        })
                        
                    except Exception as e:
                        print(f"Error processing time: {str(e)}")
                        print(traceback.format_exc())
                        message = "I couldn't understand that. Please specify a time or ask to see available slots."
                        return JsonResponse({
                            "status": "success",
                            "message": message,
                            "audio": generate_speech(message)
                        })

                # Add this after the "waiting_for_time" elif block and before "# Add other states handling here..."

                elif current_entities["conversation_state"] == "waiting_for_confirmation":
                    print(f"DEBUG - Processing confirmation. Input received: '{voice_text}'")
                    user_response = voice_text.lower().strip()
                    
                    # Check for any confirmation words
                    confirmation_words = ['yes', 'book', 'confirm', 'okay', 'sure', 'yep', 'yeah']
                    is_confirmed = any(word in user_response for word in confirmation_words)
                    
                    print(f"DEBUG - Is confirmed: {is_confirmed}")
                    
                    if is_confirmed:
                        try:
                            # Get the stored date and time
                            date = datetime.date.fromisoformat(current_entities["appointment_date"])
                            time = datetime.datetime.strptime(current_entities["appointment_time"], '%H:%M').time()
                            
                            # Create or get patient
                            patient, created = Patient.objects.get_or_create(
                                name=current_entities["patient_name"]
                            )
                            
                            # Create appointment
                            appointment = Appointment.objects.create(
                                patient=patient,
                                appointment_date=date,
                                appointment_time=time
                            )
                            
                            success_message = (
                                f"Great! I have successfully booked your appointment with the name of "
                                f"{current_entities['patient_name']} for "
                                f"{date.strftime('%A, %B %d, %Y')} at "
                                f"{time.strftime('%I:%M %p')}. Thank you for booking with us!"
                            )
                            
                            # Reset conversation state
                            current_entities["conversation_state"] = "completed"
                            request.session.modified = True
                            
                            return JsonResponse({
                                "status": "success",
                                "message": success_message,
                                "audio": generate_speech(success_message)
                            })
                            
                        except Exception as e:
                            print(f"DEBUG - Error creating appointment: {str(e)}")
                            print(traceback.format_exc())
                            error_message = "Sorry, there was an error creating your appointment. Please try again."
                            return JsonResponse({
                                "status": "success",
                                "message": error_message,
                                "audio": generate_speech(error_message)
                            })
                    
                    elif any(word in user_response for word in ['no', 'nope', 'cancel', 'don\'t']):
                        current_entities["conversation_state"] = "waiting_for_time"
                        request.session.modified = True
                        message = "Okay, let's try again. What time would you prefer?"
                        return JsonResponse({
                            "status": "success",
                            "message": message,
                            "audio": generate_speech(message)
                        })
                    
                    else:
                        message = "Please say 'yes' or 'book it' to confirm your appointment, or 'no' to cancel."
                        return JsonResponse({
                            "status": "success",
                            "message": message,
                            "audio": generate_speech(message)
                        })

                # Add other states handling here...

                return JsonResponse({
                    "status": "success",
                    "message": "I didn't understand that. Could you please repeat?",
                    "audio": generate_speech("I didn't understand that. Could you please repeat?")
                })

        except json.JSONDecodeError:
            return JsonResponse({
                "status": "error",
                "message": "Invalid JSON data"
            }, status=400)
        except Exception as e:
            print(f"Server error: {str(e)}")
            return JsonResponse({
                "status": "error",
                "message": f"Server error: {str(e)}"
            }, status=500)

    # Handle GET request
    if request.method == "GET":
        return render(request, "book_appointment.html")

    # Return error for other methods
    return JsonResponse({
        "status": "error",
        "message": "Method not allowed"
    }, status=405)

def check_db(request):
    conn = sqlite3.connect('db.sqlite3')
    cursor = conn.cursor()
    
    # Get table info
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='booking_appointment';")
    table_info = cursor.fetchone()
    
    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    response = f"Tables in database: {tables}\n\nAppointment table structure: {table_info}"
    return HttpResponse(response, content_type='text/plain')

def booking_view(request):
    if request.method == 'POST':
        if 'confirm_booking' in request.POST:
            # Final booking submission
            name = request.POST.get('name')
            date = request.POST.get('date')
            time = request.POST.get('time')
            
            # Save to database here
            # ... your existing booking save logic ...

            success_message = f"Great! I have successfully booked your appointment with the name of {name} for {date} at {time}"
            messages.success(request, success_message)
            return redirect('booking_success')  # or wherever you want to redirect
            
        else:
            # First submission - show confirmation
            context = {
                'name': request.POST.get('name'),
                'date': request.POST.get('date'),
                'time': request.POST.get('time'),
                'show_confirmation': True
            }
            return render(request, 'booking/booking_form.html', context)
    
    return render(request, 'booking/booking_form.html')