import os
import uuid
import logging
import time
from flask import Flask, request, Response, jsonify, abort
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
from elevenlabs import ElevenLabs  # Changed from ElevenLabsUser
from sheets import update_column, COLS, init_sheets
from dotenv import load_dotenv

# Load local .env when present (development)
load_dotenv()

# Config / env
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
ELEVEN_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")
TURN_LIMIT = int(os.environ.get("TURN_LIMIT", "6"))

if not all([TWILIO_SID, TWILIO_AUTH, TWILIO_NUMBER, OPENAI_KEY, ELEVEN_API_KEY]):
    raise RuntimeError("Missing required environment variables. Check .env")

# Initialize clients
twilio_client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
# Initialize ElevenLabs client
elevenlabs_client = ElevenLabs(api_key=ELEVEN_API_KEY)  # Changed initialization
app = Flask(__name__)

# Initialize sheets client
try:
    init_sheets()
except Exception as e:
    app.logger.warning("Google Sheets init failed: %s", e)

# Logging
logging.basicConfig(level=logging.INFO)

# In-memory cache for TTS audio
AUDIO_CACHE = {}
AUDIO_TTL = 300  # seconds

def cache_audio_and_get_url(text):
    """
    Generate TTS audio via ElevenLabs official SDK (eleven_multilingual_v2)
    and return a URL for Twilio to play.
    """
    key = str(uuid.uuid5(uuid.NAMESPACE_DNS, text))  # Use deterministic UUID based on text
    
    if key in AUDIO_CACHE and (time.time() - AUDIO_CACHE[key]["ts"] < AUDIO_TTL):
        # Return cached audio if still valid
        return f"{BASE_URL}/tts/{key}.mp3"
    
    # Generate new audio
    audio_bytes = elevenlabs_client.text_to_speech.convert(
        text=text,
        voice_id="laura",  # Use voice_id instead of voice name
        model_id="eleven_multilingual_v2"
    )
    
    AUDIO_CACHE[key] = {"ts": time.time(), "bytes": audio_bytes}
    return f"{BASE_URL}/tts/{key}.mp3"

@app.route("/tts/<key>.mp3", methods=["GET"])
def serve_tts(key):
    entry = AUDIO_CACHE.get(key)
    if not entry or (time.time() - entry["ts"] > AUDIO_TTL):
        abort(404)
    return Response(entry["bytes"], mimetype="audio/mpeg")

def openai_next_prompt(conversation_text):
    """
    Use OpenAI completions to generate next prompt and extract entities.
    """
    import requests, json, re
    url = "https://api.openai.com/v1/chat/completions"  # Updated endpoint
    headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    
    messages = [
        {
            "role": "system", 
            "content": "You are Maria, a warm, friendly female trucking dispatcher. Speak casually and with trucking vocabulary. Extract entities from the conversation."
        },
        {
            "role": "user",
            "content": f"Based on this transcript, extract entities: equipment, mc_number, preferred_regions, rate_expectation, interest_flag (yes/no), escalate_flag (yes/no).\n\nTranscript: {conversation_text}\n\nReturn a JSON object with fields: next_prompt, equipment, mc_number, preferred_regions, rate_expectation, interest_flag, escalate_flag.\nThe next_prompt should be a natural-sounding next sentence (<=2 sentences) for Maria to ask/say."
        }
    ]
    
    data = {
        "model": "gpt-4o-mini",
        "messages": messages,
        "max_tokens": 180,
        "temperature": 0.25,
        "response_format": {"type": "json_object"}  # Ask for JSON response
    }
    
    try:
        r = requests.post(url, headers=headers, json=data, timeout=20)
        r.raise_for_status()
        out = r.json()
        text = out["choices"][0]["message"]["content"].strip()
        
        # Parse JSON response
        payload = json.loads(text)
        
    except Exception as e:
        app.logger.exception("OpenAI parse error: %s", e)
        payload = {
            "next_prompt": "Thanks. Can I text you an onboarding link to upload MC and documents?",
            "equipment": "", 
            "mc_number": "", 
            "preferred_regions": "", 
            "rate_expectation": "", 
            "interest_flag": "no", 
            "escalate_flag": "no"
        }
    
    return payload

def safe_update_sheet(row, updates: dict):
    for k, v in updates.items():
        try:
            update_column(row, k, v)
        except Exception as e:
            app.logger.exception("Failed update sheet col %s: %s", k, e)

@app.route("/voice", methods=["POST"])
def voice_entrypoint():
    rowId = request.args.get("rowId")
    if not rowId:
        return Response("Missing rowId", status=400)
    try:
        row = int(rowId)
    except:
        return Response("Invalid rowId", status=400)

    initial_text = "Hey — is this the driver? This is Maria with GoodLane Dispatch. Are you running a box truck or a semi these days?"
    audio_url = cache_audio_and_get_url(initial_text)

    resp = VoiceResponse()
    resp.say("This call may be recorded for quality and onboarding. If that's ok, please say yes.")
    gather = Gather(input="speech", speechTimeout="auto", action=f"/gather?rowId={row}&turn=1", method="POST")
    gather.play(audio_url)
    resp.append(gather)
    resp.say("I couldn't hear you. We'll text you a link to get started.")
    resp.hangup()

    safe_update_sheet(row, {"Status": "Calling", "LastContactedUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "AgentName": "Maria"})
    return Response(str(resp), mimetype="text/xml")

@app.route("/api/start-call", methods=["POST"])
def start_call():
    data = request.json
    phone = data.get("phone")
    rowId = data.get("rowId")

    if not phone or not rowId:
        return jsonify({"error": "phone and rowId required"}), 400

    try:
        call = twilio_client.calls.create(
            to=phone,
            from_=TWILIO_NUMBER,
            url=f"{BASE_URL}/voice?rowId={rowId}"
        )
        return jsonify({"status": "queued", "callSid": call.sid})
    except Exception as e:
        app.logger.exception("Call creation failed: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route("/gather", methods=["POST"])
def gather_handler():
    rowId = request.args.get("rowId")
    if not rowId:
        return Response("Missing rowId", status=400)
    try:
        row = int(rowId)
    except:
        return Response("Invalid rowId", status=400)

    turn = int(request.args.get("turn", "1"))
    call_sid = request.form.get("CallSid")
    from_number = request.form.get("From")
    speech_result = request.form.get("SpeechResult", "").strip()

    app.logger.info("Call %s row %s turn %s speech: %s", call_sid, row, turn, speech_result)

    # Append transcript
    new_note = f"Turn {turn} ({from_number}): {speech_result}"
    safe_update_sheet(row, {"Notes": new_note})

    if turn >= TURN_LIMIT:
        send_onboarding_sms_internal(row)
        safe_update_sheet(row, {"Status": "Onboarding Sent", "OnboardingLinkSentAtUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        resp = VoiceResponse()
        resp.say("Looks like we reached the end of our quick call. I'll text you a one-minute onboarding link so you can upload your docs. Thanks!")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")

    # Generate next prompt via OpenAI
    payload = openai_next_prompt(speech_result)
    next_prompt = payload.get("next_prompt") or "Thanks. Can I text you an onboarding link to upload MC and documents?"
    interest = payload.get("interest_flag", "no").lower()
    mcnum = payload.get("mc_number", "")
    equipment = payload.get("equipment", "")

    if mcnum:
        safe_update_sheet(row, {"MC_Number": mcnum})
    if equipment:
        safe_update_sheet(row, {"Equipment": equipment})
    if interest == "yes":
        safe_update_sheet(row, {"Status": "Interested"})
        send_onboarding_sms_internal(row)
        safe_update_sheet(row, {"OnboardingLinkSentAtUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        resp = VoiceResponse()
        resp.say("Awesome — I'll text you the onboarding link now. Thanks for your time!")
        resp.hangup()
        return Response(str(resp), mimetype="text/xml")

    # Next turn
    audio_url = cache_audio_and_get_url(next_prompt)
    resp = VoiceResponse()
    gather = Gather(input="speech", speechTimeout="auto", action=f"/gather?rowId={row}&turn={turn+1}", method="POST")
    gather.play(audio_url)
    resp.append(gather)
    resp.say("I couldn't hear you. I will text you a short onboarding link so you can finish in a minute.")
    resp.hangup()
    return Response(str(resp), mimetype="text/xml")

def send_onboarding_sms_internal(row):
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        os.environ.get("GOOGLE_CREDS_JSON"),
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds)
    range_notation = f"R{row}C{COLS['Phone']}"
    try:
        res = service.spreadsheets().values().get(
            spreadsheetId=os.environ.get("GOOGLE_SHEET_ID"),
            range=range_notation
        ).execute()
        phone = res.get("values", [[]])[0][0]
    except Exception as e:
        app.logger.exception("Failed to read phone from sheet: %s", e)
        phone = None

    if not phone:
        app.logger.warning("No phone found for row %s", row)
        return False

    token = str(uuid.uuid4())
    link = f"{BASE_URL}/onboard/{token}"
    safe_update_sheet(row, {"OnboardingLinkSentAtUTC": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    try:
        sms = twilio_client.messages.create(body=f"Hey from GoodLane Dispatch — quick onboarding link: {link}", from_=TWILIO_NUMBER, to=phone)
        app.logger.info("Sent onboarding SMS SID=%s to %s", sms.sid, phone)
    except Exception as e:
        app.logger.exception("Twilio SMS send failed: %s", e)
        return False
    return True

@app.route("/onboard/<token>", methods=["GET", "POST"])
def onboard_page(token):
    if request.method == "GET":
        html = f"""
        <html><body>
        <h3>Quick Onboarding (60s)</h3>
        <form method="post" enctype="multipart/form-data">
          <input name="FirstName" placeholder="First name" required><br>
          <input name="Phone" placeholder="Phone" required><br>
          <input name="MC_Number" placeholder="MC Number"><br>
          W-9: <input type="file" name="w9" required><br>
          COI: <input type="file" name="coi" required><br>
          <button type="submit">Submit</button>
        </form>
        </body></html>
        """
        return Response(html, mimetype="text/html")
    return "Thanks — we'll review your upload and contact you soon."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
