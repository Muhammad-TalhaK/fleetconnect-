# FleetConnect — AI Voice Dispatcher 📞🤖

> An AI phone agent that makes and receives calls, holds a natural spoken conversation, and logs structured results in real time — built to automate dispatch and lead-qualification calls in the US trucking industry.

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?style=flat&logo=flask&logoColor=white)
![Twilio](https://img.shields.io/badge/Twilio-F22F46?style=flat&logo=twilio&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=flat&logo=openai&logoColor=white)
![ElevenLabs](https://img.shields.io/badge/ElevenLabs-000000?style=flat)
![Google Sheets](https://img.shields.io/badge/Google%20Sheets-34A853?style=flat&logo=googlesheets&logoColor=white)

## Overview

FleetConnect places outbound calls (and handles inbound), speaks with the person using a realistic AI voice, runs the conversation with an LLM that decides what to say turn-by-turn, and writes the outcome of each call into a Google Sheet as it happens. It's a complete voice-AI pipeline wired across telephony, speech, language, and data.

## Why it's interesting (the engineering)

- **Real-time, multi-service orchestration** — Twilio (telephony) ↔ OpenAI (dialogue logic) ↔ ElevenLabs (text-to-speech) ↔ Google Sheets (data), coordinated through Flask webhooks.
- **LLM-driven conversation state** — each turn is decided by the model and returned as **strict JSON**, so the control flow stays reliable instead of free-form.
- **Production-aware design** — webhook signature validation, externalized secrets, conversation-state handling, and per-call cost modeling are all considered.

## Architecture

```
   Caller  ⇄  Twilio Voice  ⇄  Flask server
                                  ├─ OpenAI        → decides next turn (JSON)
                                  ├─ ElevenLabs    → turns text into speech
                                  └─ Google Sheets → logs the call outcome
```

## Features

- Outbound **and** inbound call flows via Twilio webhooks
- LLM-driven dialogue with structured (JSON) turn decisions
- Natural text-to-speech via ElevenLabs (provider is pluggable)
- Live logging of call results to Google Sheets
- Deployable to Render / Heroku / any VPS with an HTTPS domain

## Tech stack

`Python` · `Flask` · `Twilio Voice API` · `OpenAI API` · `ElevenLabs API` · `Google Sheets API`

## How it works (high level)

1. A call connects and Twilio hits the Flask `/voice` webhook.
2. The server asks the LLM for the next thing to say (returned as strict JSON).
3. That text is synthesized to speech (ElevenLabs) and played to the caller.
4. The caller's reply is transcribed and the loop continues, turn by turn.
5. When the call ends, the structured outcome is written to Google Sheets.


## Running it (overview)

Requires accounts/keys for Twilio, OpenAI, ElevenLabs, and a Google Cloud service account for Sheets. Copy `.env.example` → `.env`, fill in the credentials, deploy to a host with a public HTTPS domain, and point your Twilio number's voice webhook at `/voice`. **Secrets are never committed** — they live in your host's secret storage.

## Production notes

- Validate Twilio request signatures (`X-Twilio-Signature`) on every webhook.
- Move generated TTS audio to object storage (e.g., S3) and serve over HTTPS for scale.
- Use Redis or a DB for conversation state rather than in-memory.
- Enable call recording where appropriate and store the recording URL.

---

> **About this repository.** This is a *showcase* version. The conversation prompts and tuned dialogue logic are redacted, and every credential is externalized to environment variables. The architecture, integration patterns, and control flow are intact and representative of the working system.

> ⚖️ **Responsible use.** Built as a personal project for automating routine dispatch and qualification calls. Use in compliance with telephony, recording-consent, and do-not-call regulations in your jurisdiction.
