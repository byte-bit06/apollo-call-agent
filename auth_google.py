import os
import glob
import json
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import sys
import webbrowser

# Load existing environment variables
from dotenv import load_dotenv, set_key
load_dotenv()

PORT = 80
REDIRECT_URI = "http://localhost"

# Find client secret file in the directory
secret_files = glob.glob("client_secret_*.json")
if not secret_files:
    # Check if there's any file named client_secret.json
    if os.path.exists("client_secret.json"):
        secret_files = ["client_secret.json"]
    else:
        print("Error: Could not find any client_secret_*.json file in the RAG directory.")
        print("Please download your Web OAuth client secrets JSON file from Google Cloud Console and save it here.")
        sys.exit(1)

client_secret_file = secret_files[0]
print(f"Using Client Secret file: {client_secret_file}")

with open(client_secret_file, "r") as f:
    secret_data = json.load(f)

# The file can have top level key "web" or "installed"
config_key = "web" if "web" in secret_data else "installed" if "installed" in secret_data else None
if not config_key:
    print("Error: Invalid client secrets JSON format. Missing 'web' or 'installed' key.")
    sys.exit(1)

client_config = secret_data[config_key]
CLIENT_ID = client_config["client_id"]
CLIENT_SECRET = client_config["client_secret"]
AUTH_URI = client_config.get("auth_uri", "https://accounts.google.com/o/oauth2/auth")
TOKEN_URI = client_config.get("token_uri", "https://oauth2.googleapis.com/token")

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        
        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html>
                <head><title>Authorization Successful</title></head>
                <body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
                    <h2 style="color: #4CAF50;">Authorization Successful!</h2>
                    <p>You can close this browser window and return to the terminal.</p>
                </body>
                </html>
            """)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization code not found in request.")

def run_local_server():
    server = HTTPServer(("localhost", PORT), OAuthCallbackHandler)
    print(f"Waiting for authorization callback on {REDIRECT_URI}...")
    # Wait until one request is handled
    server.handle_request()
    return OAuthCallbackHandler.auth_code

def main():
    print("\n========================================================")
    print("      Google Calendar OAuth Authorization Helper")
    print("========================================================\n")
    
    # 1. Construct authorization URL
    scopes = ["https://www.googleapis.com/auth/calendar"]
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline", # Crucial to get a refresh token!
        "prompt": "consent"       # Forces consent screen to ensure refresh token is returned
    }
    auth_url = f"{AUTH_URI}?{urllib.parse.urlencode(params)}"
    
    print("Please authorize this application by opening this URL in your web browser:")
    print(f"\n{auth_url}\n")
    
    # Try to open in browser automatically
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
        
    # 2. Start local server to capture authorization code
    code = run_local_server()
    if not code:
        print("Error: Did not receive authorization code.")
        sys.exit(1)
        
    print(f"Authorization code received: {code[:10]}...")
    
    # 3. Exchange code for access & refresh tokens
    print("Exchanging authorization code for tokens...")
    token_params = {
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    
    data = urllib.parse.urlencode(token_params).encode("utf-8")
    req = urllib.request.Request(TOKEN_URI, data=data, method="POST")
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            refresh_token = res_data.get("refresh_token")
            
            if not refresh_token:
                print("\nError: Did not receive a refresh_token.")
                print("Make sure you unlinked/revoked the application in Google Account settings if you authorized it before, so Google shows the consent prompt again.")
                sys.exit(1)
                
            # 4. Save to .env
            env_path = ".env"
            set_key(env_path, "GOOGLE_CLIENT_ID", CLIENT_ID)
            set_key(env_path, "GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
            set_key(env_path, "GOOGLE_REFRESH_TOKEN", refresh_token)
            
            print("\n========================================================")
            print("🎉 SUCCESS! Google Calendar Credentials Saved to .env:")
            print(f"- GOOGLE_CLIENT_ID: {CLIENT_ID[:15]}...")
            print(f"- GOOGLE_CLIENT_SECRET: {CLIENT_SECRET[:10]}...")
            print(f"- GOOGLE_REFRESH_TOKEN: {refresh_token[:15]}...")
            print("========================================================\n")
            print("Your webhook server will automatically pick up these credentials on its next request and connect to your live Google Calendar!")
            
    except Exception as e:
        print(f"Error exchanging token: {e}")
        if hasattr(e, "read"):
            print(f"Details: {e.read().decode('utf-8')}")

if __name__ == "__main__":
    main()
