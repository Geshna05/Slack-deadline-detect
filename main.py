import os
import json
import smtplib
import torch
import pyttsx3
import psycopg2
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.signature import SignatureVerifier
from transformers import pipeline
from plyer import notification
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from tkinter import Tk, Button
from tkcalendar import Calendar
import tkinter.simpledialog as simpledialog
from tkinter import Label, Entry, StringVar, OptionMenu
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()


# ==================== SLACK SETUP ====================
slack_token = os.getenv("SLACK_BOT_TOKEN")
client = WebClient(token=slack_token)
signature_verifier = SignatureVerifier(signing_secret=os.getenv("SLACK_SIGNING_SECRET"))


# ==================== NLP INTENT CLASSIFIER ====================
device = 0 if torch.cuda.is_available() else -1
print("Device set to use", "GPU" if device == 0 else "CPU")
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli", device=device)
labels = ["reminder", "deadline", "event update", "greeting", "casual"]

# ==================== FLASK APP ====================
app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

# ==================== LOGGING ====================
def log_reminder(message, sender_name, intent):
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        log_directory = "logs"
        if not os.path.exists(log_directory):
            os.makedirs(log_directory)
        log_file = os.path.join(log_directory, f"{current_date}_reminders.txt")
        with open(log_file, "a") as file:
            log_entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {sender_name} - {intent} - {message}\n"
            file.write(log_entry)
        print("✅ Logged reminder.")
    except Exception as e:
        print(f"⚠️ Error logging reminder: {e}")

# ==================== LOGGING ====================
def log_reminder_to_db(message, sender_name):
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"), 
            user=os.getenv("DB_USER"),  
            password=os.getenv("DB_PASSWORD"),  
            host="localhost",
            port=os.getenv("DB_PORT")
        )

        cur = conn.cursor()

        # Now, we are inserting only message, sender_name, and remind_at columns
        cur.execute(""" 
            INSERT INTO reminders (message, sender_name, remind_at) 
            VALUES (%s, %s, %s)
        """, (message, sender_name, datetime.now()))  # Use actual time here

        conn.commit()
        cur.close()
        conn.close()

        print("✅ Reminder logged to the database.")

    except Exception as e:
        print(f"⚠️ Error logging reminder to database: {e}")


# ==================== REMINDER NOTIFICATIONS ====================
def show_reminder(message, sender_name="Unknown User"):
    print(f"🔔 Deadline Detected from {sender_name}!")

    try:
        engine = pyttsx3.init()
        engine.setProperty('rate', 170)
        engine.say("Reminder alert from " + sender_name)
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        print(f"⚠️ Voice alert error: {e}")

    try:
        notification.notify(
            title=f"Slack Reminder from {sender_name}",
            message=message,
            timeout=10
        )
    except Exception as e:
        print(f"⚠️ Notification error: {e}")

    log_reminder(message, sender_name, "Reminder")
    log_reminder_to_db(message, sender_name)
    ask_to_schedule(message, sender_name)

# ==================== ASK TO SCHEDULE ====================
def ask_to_schedule(message, sender_name):
    try:
        response = input(f"📅 Do you want to schedule this reminder from {sender_name}: '{message}'? (yes/no): ").strip().lower()
        if response == "yes":
            root = Tk()
            root.title("Schedule Reminder")
            root.geometry("320x400")

            cal = Calendar(root, selectmode="day", date_pattern="yyyy-mm-dd")
            cal.pack(pady=10)

            Label(root, text="Hour (1-12):").pack()
            hour_entry = Entry(root)
            hour_entry.pack()

            Label(root, text="Minute (0-59):").pack()
            minute_entry = Entry(root)
            minute_entry.pack()

            Label(root, text="AM/PM:").pack()
            period_var = StringVar(root)
            period_var.set("AM")
            period_menu = OptionMenu(root, period_var, "AM", "PM")
            period_menu.pack()

            def on_submit():
                selected_date = cal.get_date()
                hour = hour_entry.get().zfill(2)
                minute = minute_entry.get().zfill(2)
                period = period_var.get()

                try:
                    time_str = f"{hour}:{minute} {period}"
                    dt_str = f"{selected_date} {time_str}"
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")
                    scheduler.add_job(send_email_notification, 'date', run_date=dt, args=["Slack Scheduled Reminder", f"{message}\n\nFrom: {sender_name}"])
                    print("📧 Email scheduled.")
                    add_to_calendar(f"{message} (From: {sender_name})", dt)
                    root.destroy()
                except Exception as e:
                    print(f"⚠️ Invalid time format: {e}")

            Button(root, text="Schedule Reminder", command=on_submit).pack(pady=15)
            root.mainloop()

    except Exception as e:
        print(f"⚠️ Scheduling error: {e}")

# ==================== EMAIL NOTIFICATION ====================
def send_email_notification(subject, message):
    sender = os.getenv("EMAIL_SENDER")
    receiver = os.getenv("EMAIL_RECEIVER")
    password = os.getenv("EMAIL_PASSWORD")

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = subject
    msg.attach(MIMEText(message, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())
        server.quit()
        print("✅ Email sent")
    except Exception as e:
        print(f"❌ Email error: {e}")

# ==================== GOOGLE CALENDAR ====================
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

def get_calendar_service():
    creds = None
    google_token_path = os.getenv("GOOGLE_API_TOKEN_PATH")  # Fetch token path from .env
    if os.path.exists(google_token_path):
        creds = Credentials.from_authorized_user_file(google_token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            google_credentials_path = os.getenv("GOOGLE_API_CREDENTIALS_PATH")  # Fetch credentials path from .env
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(google_credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(google_token_path, "w") as token:
            token.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


# ==================== SLACK EVENT HANDLER ====================
processed_event_ids = set()

def get_username(user_id):
    try:
        response = client.users_info(user=user_id)
        return response["user"]["real_name"] or response["user"]["name"]
    except SlackApiError as e:
        print(f"⚠️ Error fetching user info: {e}")
        return user_id

@app.route("/slack/events", methods=["POST"])
def slack_events():
    if not signature_verifier.is_valid_request(request.get_data(), request.headers):
        return "Request verification failed", 400

    try:
        data = request.get_json(force=True)

        if data.get("type") == "url_verification":
            return jsonify({"challenge": data["challenge"]})

        if "event" in data:
            event = data["event"]
            event_id = data.get("event_id", "")
            if event_id in processed_event_ids:
                return "Already processed", 200
            processed_event_ids.add(event_id)

            user_id = event.get("user", "")
            text = event.get("text", "")
            sender_name = get_username(user_id) if user_id else "Unknown User"

            if user_id and text:
                result = classifier(text, labels)
                top_intent = result["labels"][0]
                print(f"🎯 Intent: {top_intent} from {sender_name}")
                if top_intent in ["reminder", "deadline", "event update"]:
                    show_reminder(text, sender_name)

        return "OK", 200

    except Exception as e:
        print("❌ Error handling event:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False, port=5000)
    #log_reminder_to_db("Test reminder message", "Test User", "reminder")
