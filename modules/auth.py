from dotenv import load_dotenv
import os
from .config import BASE_URL

load_dotenv()
ID = os.environ["SEOULAIHUB_ID"]
PW = os.environ["SEOULAIHUB_PW"]


def login(page, redirect_url: str):
    login_url = f"{BASE_URL}/login/login.asp?refer={redirect_url}"
    page.goto(login_url)
    page.wait_for_load_state("networkidle")
    page.fill("#u_id", ID)
    page.fill("#u_pwd", PW)
    page.press("#u_pwd", "Enter")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)
