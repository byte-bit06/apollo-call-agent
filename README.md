# Apollo Call Agent

An AI-powered outbound sales dialer that automatically discovers Australian business leads and calls them using Vapi AI voice agents, with Google Calendar integration for booking appointments.

## Features

- 🔍 **Lead Discovery** — Scrapes DuckDuckGo and business websites using Gemini AI to extract structured profiles (name, phone, services, pain points)
- 📞 **Automated Calling** — Triggers Vapi outbound calls with dynamic, lead-specific system prompts
- 🤖 **Auto-Pilot Mode** — Continuously finds and calls leads within business hours (9am–5pm)
- 📅 **Google Calendar** — AI agent can check availability and book appointments in real time
- 🔐 **Authentication** — Session-based login protecting the dashboard
- 📊 **Dashboard** — Light-themed professional UI with lead management, call logs, and live autopilot terminal

## Tech Stack

- **Backend**: Python, FastAPI, Uvicorn
- **Frontend**: Vanilla HTML/CSS/JS (no framework)
- **AI**: Google Gemini (lead extraction + system prompt), Vapi (voice calling)
- **Calendar**: Google Calendar API

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/byte-bit06/apollo-call-agent.git
cd apollo-call-agent
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

### 4. Run the server

```bash
python outbound_server.py
```

Visit [http://localhost:8080](http://localhost:8080) and sign in with your credentials.

## Environment Variables

See `.env.example` for all required variables.

## Deployment

This app is a FastAPI server. It can be deployed to any platform that supports Python:

- **Railway** — Connect GitHub repo, set env vars, deploy
- **Render** — Free tier available, set start command to `python outbound_server.py`
- **VPS** — Run behind Nginx reverse proxy

> **Note**: Cloudflare Tunnels can expose your local server to the internet without a VPS.

## Dashboard Login

Default credentials (change in `outbound_server.py`):
- **Username**: `apollo`
- **Password**: *(set in server config)*

## Webhook

Vapi sends call outcome webhooks to:
```
POST /webhook
```

Configure this URL in your Vapi dashboard under the assistant settings.
