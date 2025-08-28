# ping.py
from flask import Flask
app = Flask(__name__)
@app.get("/healthz")
def h(): return "ok", 200
