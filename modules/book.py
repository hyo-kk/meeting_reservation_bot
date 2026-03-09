import re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from .config import BASE_URL, SITE_IDS
from .auth import login


def find_site_id(site_name: str) -> str | None:
    site_name_clean = site_name.replace(" ", "")
    for key, val in SITE_IDS.items():
        if site_name_clean in key.replace(" ", "") or key.replace(" ", "") in site_name_clean:
            return val
    return None


def parse_time_range(time_str: str) -> list[str]:
    """'09:00-10:30' -> ['09:00', '09:30', '10:00'] (시작 시간 목록)"""
    parts = time_str.replace(" ", "").split("-")
    if len(parts) != 2:
        raise ValueError(f"시간 형식 오류: '{time_str}' (예: 09:00-10:30)")
    start_h, start_m = map(int, parts[0].split(":"))
    end_h, end_m = map(int, parts[1].split(":"))
    start_total = start_h * 60 + start_m
    end_total = end_h * 60 + end_m
    if start_total >= end_total:
        raise ValueError("시작 시간이 종료 시간보다 늦습니다.")
    if (end_total - start_total) % 30 != 0:
        raise ValueError("시간은 30분 단위여야 합니다.")
    slots = []
    t = start_total
    while t < end_total:
        slots.append(f"{t//60:02d}:{t%60:02d}")
        t += 30
    return slots


def format_date_for_selector(date_str: str) -> str:
    """'2026-03-09' -> '2026-3-09' (사이트 달력 형식)"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{d.year}-{d.month}-{d.day:02d}"


def get_facilities(scr_id: str) -> list[dict]:
    """사이트 ID로 시설 목록 조회 (getOfficeList.asp 호출)"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            login(page, "%2Fboard%2Fboard_reserve%2Fboard_list.asp%3FscrID%3D0000000196%26pageNum%3D3%26subNum%3D2%26ssubNum%3D1")
            result = page.evaluate(f'''
                async () => {{
                    const resp = await fetch("/board/board_reserve/getOfficeList.asp", {{
                        method: "POST",
                        headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
                        body: "scrID={scr_id}"
                    }});
                    return await resp.text();
                }}
            ''')
            facilities = re.findall(
                r"<li\s+data-bdnum=['\"]([^'\"]+)['\"]\s+data-type=['\"]([^'\"]+)['\"][^>]*>\s*<a[^>]*>(.*?)</a>",
                result, re.DOTALL
            )
            return [{"bdnum": b, "type": t, "name": n.strip()} for b, t, n in facilities]
        except Exception:
            return []
        finally:
            browser.close()


def reserve(site: str, date: str, facility: str, time_range: str, dry_run: bool = False) -> bool:
    scr_id = find_site_id(site)
    if not scr_id:
        print(f"❌ 알 수 없는 사이트: '{site}'")
        print(f"   가능한 사이트: {', '.join(SITE_IDS.keys())}")
        return False

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        print(f"❌ 날짜 형식 오류: '{date}' (예: 2026-03-09)")
        return False

    try:
        time_slots = parse_time_range(time_range)
    except ValueError as e:
        print(f"❌ {e}")
        return False

    date_selector = format_date_for_selector(date)
    print(f"📅 예약 시도: {site} / {facility} / {date} / {time_range}")
    print(f"   선택 슬롯: {', '.join(time_slots)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            print("🔐 로그인 중...")
            login(page, "%2Fboard%2Fboard_reserve%2Fboard_list.asp%3FscrID%3D0000000196%26pageNum%3D3%26subNum%3D2%26ssubNum%3D1")

            print(f"🏢 사이트 선택: {site}")
            try:
                with page.expect_response("**/getCalendar.asp", timeout=10000):
                    page.click(f'li[data-scrid="{scr_id}"] a')
            except PlaywrightTimeout:
                print("❌ 달력을 불러오지 못했습니다.")
                return False
            page.wait_for_timeout(500)

            print(f"📆 날짜 선택: {date}")
            date_li = page.query_selector(f'#idCalendar li[data-date="{date_selector}"]')
            if not date_li:
                print(f"❌ 현재 달력에서 '{date}'를 찾을 수 없습니다.")
                return False

            li_class = date_li.get_attribute("class") or ""
            if "dev_pointer" not in li_class:
                print(f"❌ '{date}'은 예약 불가능한 날짜입니다 (주말/공휴일 또는 예약 마감).")
                return False

            try:
                with page.expect_response("**/getOfficeList.asp", timeout=10000):
                    date_li.click()
            except PlaywrightTimeout:
                print("❌ 시설 목록을 불러오지 못했습니다.")
                return False
            page.wait_for_timeout(500)

            print(f"🚪 시설 선택: {facility}")
            facilities_els = page.query_selector_all(".fac03_flow02_list li")
            target_fac = None
            available_facs = []
            for fac in facilities_els:
                name = fac.inner_text().strip()
                available_facs.append(name)
                if name == facility or facility in name:
                    target_fac = fac
                    break

            if not target_fac:
                print(f"❌ 시설 '{facility}'을 찾을 수 없습니다.")
                print(f"   가능한 시설: {', '.join(available_facs)}")
                return False

            if target_fac.get_attribute("data-type") != "A":
                print(f"⚠️  '{facility}'은 담당자 문의가 필요한 시설입니다 (온라인 예약 불가).")
                return False

            try:
                with page.expect_response("**/getTimeTable.asp", timeout=10000):
                    target_fac.click()
            except PlaywrightTimeout:
                print("❌ 시간표를 불러오지 못했습니다.")
                return False
            page.wait_for_timeout(1000)

            remain_el = page.query_selector("#remainTime")
            remain_text = (remain_el.inner_text() if remain_el else "").strip()
            print(f"⏰ {remain_text}")

            unavailable = []
            for s in time_slots:
                if not page.query_selector(f'.fac03_flow04_list li[data-stime="{s}"]'):
                    unavailable.append(s)

            if unavailable:
                print(f"❌ 다음 시간은 이미 예약되어 있습니다: {', '.join(unavailable)}")
                return False

            remain_match = re.search(r'(\d+):(\d+)', remain_text)
            if remain_match:
                remain_min = int(remain_match.group(1)) * 60 + int(remain_match.group(2))
                needed_min = len(time_slots) * 30
                if needed_min > remain_min:
                    print(f"❌ 잔여 시간 부족: 필요 {needed_min}분, 잔여 {remain_min}분")
                    return False

            if time_slots:
                last_h, last_m = map(int, time_slots[-1].split(":"))
                end_m = last_m + 30
                end_h = last_h + end_m // 60
                end_str = f"{end_h:02d}:{end_m % 60:02d}"
                print(f"🕐 시간 선택: {time_slots[0]} ~ {end_str}")
            for s in time_slots:
                page.click(f'.fac03_flow04_list li[data-stime="{s}"] a')
                page.wait_for_timeout(150)

            remain_after = (remain_el.inner_text() if remain_el else "").strip()
            print(f"⏰ 선택 후 {remain_after}")

            print("➡️  예약 정보 확인 페이지로 이동...")
            next_btn = page.query_selector(".fac03_bt a")
            if not next_btn:
                print("❌ '다음 단계로' 버튼을 찾을 수 없습니다.")
                return False

            next_btn.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            print("✅ 예약 정보 확인:")
            info_items = page.query_selector_all(".fac03_info li, .reserve_info li, table tr")
            for item in info_items[:10]:
                text = item.inner_text().strip()
                if text:
                    print(f"   {text}")

            if dry_run:
                print("\n⚠️  dry_run 모드: 실제 예약 신청 없이 종료합니다.")
                page.screenshot(path="reservation_preview.png")
                print("   미리보기: reservation_preview.png")
                return True

            print("\n📝 예약 신청 중...")
            page.on("dialog", lambda dialog: dialog.accept())
            submit_btn = page.query_selector(".fac03_bt a")
            if not submit_btn:
                print("❌ '예약 신청' 버튼을 찾을 수 없습니다.")
                return False

            submit_btn.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            page.screenshot(path="reservation_result.png")

            if "board_list.asp" in page.url or "board_list_check" in page.url or "board_check.asp" not in page.url:
                print("🎉 예약이 완료되었습니다!")
            else:
                print("⚠️  예약 처리 중 문제가 발생했습니다. 결과 스크린샷을 확인해주세요.")
            print("   결과 스크린샷: reservation_result.png")
            return True

        except Exception as e:
            print(f"❌ 오류: {e}")
            page.screenshot(path="error.png")
            return False
        finally:
            browser.close()
