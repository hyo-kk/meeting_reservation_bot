from playwright.sync_api import sync_playwright

from .config import BASE_URL
from .auth import login


def cancel_reservation(bd_num: str) -> bool:
    """예약번호로 예약 취소"""
    print(f"🗑️  예약 취소 시도: {bd_num}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            login(page, "%2Fboard%2Fboard_reserve%2Fboard_weekly.asp%3FscrID%3D0000000178")
            page.wait_for_timeout(1000)

            cancel_url = f"{BASE_URL}/board/board_reserve/board_proc_time_del.asp?bd_num={bd_num}"
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(cancel_url)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            page.screenshot(path="cancel_result.png")
            print("✅ 예약이 취소되었습니다.")
            return True

        except Exception as e:
            print(f"❌ 취소 중 오류: {e}")
            return False
        finally:
            browser.close()
