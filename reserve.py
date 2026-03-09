"""
서울 AI 허브 미팅룸 예약 자동화 스크립트

사용법:
  # 예약
  python reserve.py --site "서울 AI 허브" --date 2026-03-09 --facility "3층 미팅룸1" --time "09:00-10:00"

  # 예약 목록 확인 (이번 달)
  python reserve.py --list

  # 특정 날짜 예약 현황 조회 (전체 시설)
  python reserve.py --list --date 2026-03-09

  # 예약 취소 (bd_num은 --list로 확인)
  python reserve.py --cancel-num 00000001962026003406
"""
import argparse
import sys
import re
import calendar as cal_module
from datetime import datetime, date as date_class, timedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from dotenv import load_dotenv
import os

load_dotenv()
ID = os.environ["SEOULAIHUB_ID"]
PW = os.environ["SEOULAIHUB_PW"]
COMPANY_NAME = "피아스페이스(주)"

BASE_URL = "https://www.seoulaihub.kr"
RESERVE_URL = f"{BASE_URL}/board/board_reserve/board_list.asp?scrID=0000000196&pageNum=3&subNum=2&ssubNum=1"
WEEKLY_URL = f"{BASE_URL}/board/board_reserve/board_weekly.asp?scrID=0000000178"

SITE_IDS = {
    "서울 AI 허브": "0000000217",
    "한국교총": "0000000180",
    "하이브랜드": "0000000181",
    "희경빌딩 B·C동": "0000000188",
    "희경빌딩 D·E동": "0000000199",
    "희경빌딩 F동": "0000000200",
}

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

def login(page, redirect_url: str):
    login_url = f"{BASE_URL}/login/login.asp?refer={redirect_url}"
    page.goto(login_url)
    page.wait_for_load_state("networkidle")
    page.fill("#u_id", ID)
    page.fill("#u_pwd", PW)
    page.press("#u_pwd", "Enter")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)


# ─────────────────────────────────────────────
# 예약
# ─────────────────────────────────────────────
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

            # 사이트 선택
            print(f"🏢 사이트 선택: {site}")
            try:
                with page.expect_response("**/getCalendar.asp", timeout=10000):
                    page.click(f'li[data-scrid="{scr_id}"] a')
            except PlaywrightTimeout:
                print("❌ 달력을 불러오지 못했습니다.")
                return False
            page.wait_for_timeout(500)

            # 날짜 선택
            print(f"📆 날짜 선택: {date}")
            date_li = page.query_selector(f'#idCalendar li[data-date="{date_selector}"]')
            if not date_li:
                # 달력 월 이동 필요할 수 있음
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

            # 시설 선택
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

            # 잔여 시간 확인
            remain_el = page.query_selector("#remainTime")
            remain_text = (remain_el.inner_text() if remain_el else "").strip()
            print(f"⏰ {remain_text}")

            # 시간 슬롯 가용 여부 확인
            unavailable = []
            for s in time_slots:
                if not page.query_selector(f'.fac03_flow04_list li[data-stime="{s}"]'):
                    unavailable.append(s)

            if unavailable:
                print(f"❌ 다음 시간은 이미 예약되어 있습니다: {', '.join(unavailable)}")
                return False

            # 잔여 시간이 요청 시간보다 적은지 확인
            remain_match = re.search(r'(\d+):(\d+)', remain_text)
            if remain_match:
                remain_min = int(remain_match.group(1)) * 60 + int(remain_match.group(2))
                needed_min = len(time_slots) * 30
                if needed_min > remain_min:
                    print(f"❌ 잔여 시간 부족: 필요 {needed_min}분, 잔여 {remain_min}분")
                    return False

            # 슬롯 클릭
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

            # 다음 단계
            print("➡️  예약 정보 확인 페이지로 이동...")
            next_btn = page.query_selector(".fac03_bt a")
            if not next_btn:
                print("❌ '다음 단계로' 버튼을 찾을 수 없습니다.")
                return False

            next_btn.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            print("✅ 예약 정보 확인:")
            # 예약 정보 출력
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

            # 예약 신청
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
            result_text = page.inner_text("body")

            # 제출 후 board_list.asp(STEP1) 또는 board_list_check.asp로 리다이렉트 = 성공
            if "board_list.asp" in page.url or "board_list_check" in page.url or "board_check.asp" not in page.url:
                print(f"🎉 예약이 완료되었습니다!")
            else:
                print(f"⚠️  예약 처리 중 문제가 발생했습니다. 결과 스크린샷을 확인해주세요.")
            print(f"   결과 스크린샷: reservation_result.png")
            return True

        except Exception as e:
            print(f"❌ 오류: {e}")
            page.screenshot(path="error.png")
            return False
        finally:
            browser.close()


# ─────────────────────────────────────────────
# 예약 목록 조회
# ─────────────────────────────────────────────

def _get_facilities_for_site(page, scr_id: str) -> list[dict]:
    """기존 페이지 컨텍스트에서 특정 건물의 시설 목록 조회"""
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


def _parse_reservations_from_html(html: str, bd_id: str, facility_name: str, day: str) -> list[dict]:
    """board_weekly_data.asp 응답 HTML에서 예약 목록 파싱"""
    items = re.findall(
        r'<span class="company-name">(.*?)</span>(.*?)(?=<span class="company-name">|</ul>)',
        html, re.DOTALL
    )
    reservations = []
    for company, block in items:
        company = company.strip()
        bd_num_match = re.search(r'id="vtb_(\d+)_0"', block)
        if not bd_num_match:
            continue
        bd_num = bd_num_match.group(1)
        times = re.findall(rf'id="vtb_{bd_num}_\d+">\s*([0-9:]+)-([0-9:]+)\s*<', html)
        start_time = times[0][0] if times else "?"
        end_time = times[-1][1] if times else "?"
        reservations.append({
            "bd_num": bd_num,
            "date": day,
            "facility_id": bd_id,
            "facility_name": facility_name,
            "company": company,
            "start": start_time,
            "end": end_time,
            "is_mine": COMPANY_NAME in company,
        })
    return reservations


def _batch_fetch_weekly_data(page, queries: list) -> list:
    """(bd_id, day) 목록을 Promise.all로 병렬 조회. HTML 문자열 목록 반환."""
    if not queries:
        return []
    fetches = [
        f'fetch("./board_weekly_data.asp",{{method:"POST",headers:{{"Content-Type":"application/x-www-form-urlencoded"}},body:"bd_day={day}&bd_id={bd_id}&s_own="}})'
        for bd_id, day in queries
    ]
    js = f"async () => {{ const r = await Promise.all([{','.join(fetches)}]); return await Promise.all(r.map(x=>x.text())); }}"
    return page.evaluate(js)


def _fetch_day_reservations(page, bd_id: str, day: str) -> list[dict]:
    """특정 방+날짜의 예약 목록 반환"""
    result = page.evaluate(f'''
        async () => {{
            const resp = await fetch("./board_weekly_data.asp", {{
                method: "POST",
                headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
                body: "bd_day={day}&bd_id={bd_id}&s_own="
            }});
            return await resp.text();
        }}
    ''')

    # 시설명 추출 (헤더에서)
    facility_name_match = re.search(r'<h3>.*?<span[^>]*>(.*?)</span>', result, re.DOTALL)
    facility_name = facility_name_match.group(1).strip() if facility_name_match else bd_id

    items = re.findall(
        r'<span class="company-name">(.*?)</span>(.*?)(?=<span class="company-name">|</ul>)',
        result, re.DOTALL
    )
    reservations = []
    for company, block in items:
        company = company.strip()
        bd_num_match = re.search(r'id="vtb_(\d+)_0"', block)
        if not bd_num_match:
            continue
        bd_num = bd_num_match.group(1)
        times = re.findall(rf'id="vtb_{bd_num}_\d+">\s*([0-9:]+)-([0-9:]+)\s*<', result)
        start_time = times[0][0] if times else "?"
        end_time = times[-1][1] if times else "?"
        reservations.append({
            "bd_num": bd_num,
            "date": day,
            "facility_id": bd_id,
            "facility_name": facility_name,
            "company": company,
            "start": start_time,
            "end": end_time,
            "is_mine": COMPANY_NAME in company,
        })
    return reservations


def list_reservations(year: int = None, month: int = None, date: str = None) -> list[dict]:
    """예약 목록 조회. 전체 건물 대상. date(YYYY-MM-DD) 지정 시 해당 날짜만 조회."""
    now = datetime.now()
    if date:
        target_dates = [date]
    else:
        year = year or now.year
        month = month or now.month
        last_day = cal_module.monthrange(year, month)[1]
        start = max(now.date(), date_class(year, month, 1))
        end = date_class(year, month, last_day)
        target_dates = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:  # 주말 제외
                target_dates.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)

    reservations = []
    seen_bd_nums = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            login(page, "%2Fboard%2Fboard_reserve%2Fboard_weekly.asp%3FscrID%3D0000000178")
            page.wait_for_timeout(1000)

            # 모든 건물의 온라인 예약 가능 시설 수집
            all_facilities = {}  # bdnum -> {"name": ..., "site": ...}
            for site_name, scr_id in SITE_IDS.items():
                for fac in _get_facilities_for_site(page, scr_id):
                    if fac["type"] == "A":
                        all_facilities[fac["bdnum"]] = {"name": fac["name"], "site": site_name}

            # (bd_id, day) 조합 생성 후 배치 병렬 조회
            queries = [(bd_id, day) for bd_id in all_facilities for day in target_dates]
            BATCH_SIZE = 50
            for i in range(0, len(queries), BATCH_SIZE):
                batch = queries[i:i + BATCH_SIZE]
                results = _batch_fetch_weekly_data(page, batch)
                for (bd_id, day), html in zip(batch, results):
                    fac_info = all_facilities[bd_id]
                    for r in _parse_reservations_from_html(html, bd_id, fac_info["name"], day):
                        if r["bd_num"] not in seen_bd_nums:
                            seen_bd_nums.add(r["bd_num"])
                            reservations.append(r)

        finally:
            browser.close()

    return reservations


def print_reservations(year: int = None, month: int = None, date: str = None):
    now = datetime.now()
    if date:
        d = datetime.strptime(date, "%Y-%m-%d")
        label = f"{d.year}년 {d.month}월 {d.day}일"
    else:
        year = year or now.year
        month = month or now.month
        label = f"{year}년 {month}월"

    print(f"🔍 {label} 예약 현황 조회 중...")
    reservations = list_reservations(year, month, date=date)

    if not reservations:
        print("   예약 내역이 없습니다.")
        return

    # 날짜별, 시설별로 그룹핑
    from collections import defaultdict
    grouped: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in reservations:
        grouped[r["date"]][r["facility_name"]].append(r)

    my_reservations = [r for r in reservations if r["is_mine"]]

    if date:
        # 날짜 지정 시: 해당 날짜의 전체 예약 현황 표시
        print(f"\n📋 {label} 예약 현황:")
        for fac_name, items in sorted(grouped[date].items()):
            print(f"\n  🚪 {fac_name}")
            for r in sorted(items, key=lambda x: x["start"]):
                marker = " ◀ 우리 예약" if r["is_mine"] else ""
                print(f"     {r['start']} ~ {r['end']}  ({r['company']}){marker}")
    else:
        # 월 조회 시: 우리 회사 예약만 표시
        print(f"\n📋 {COMPANY_NAME} 예약 목록 ({len(my_reservations)}건):")
        for i, r in enumerate(sorted(my_reservations, key=lambda x: (x["date"], x["start"])), 1):
            print(f"   {i}. {r['date']}  {r['start']} ~ {r['end']}  {r['facility_name']}  예약번호: {r['bd_num']}")

    if my_reservations:
        print("\n   취소하려면: python reserve.py --cancel-num <예약번호>")


# ─────────────────────────────────────────────
# 예약 취소
# ─────────────────────────────────────────────
def cancel_reservation(bd_num: str) -> bool:
    """예약번호로 전체 취소"""
    print(f"🗑️  예약 취소 시도: {bd_num}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            login(page, "%2Fboard%2Fboard_reserve%2Fboard_weekly.asp%3FscrID%3D0000000178")
            page.wait_for_timeout(1000)

            # 취소 URL로 직접 이동 (zeroframe이 처리하는 URL을 직접 호출)
            cancel_url = f"{BASE_URL}/board/board_reserve/board_proc_time_del.asp?bd_num={bd_num}"
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(cancel_url)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            result_text = page.inner_text("body")
            page.screenshot(path="cancel_result.png")

            # board_proc_time_del.asp 호출 후 빈 페이지 또는 리다이렉트 = 정상 취소
            # (사이트는 취소 후 빈 응답을 반환함)
            print(f"✅ 예약이 취소되었습니다.")
            return True

        except Exception as e:
            print(f"❌ 취소 중 오류: {e}")
            return False
        finally:
            browser.close()


# ─────────────────────────────────────────────
# 시설 목록 동적 조회
# ─────────────────────────────────────────────

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
            # <li data-bdnum='...' data-type='A'><a ...>시설명</a></li>  (단/쌍따옴표 모두 처리)
            facilities = re.findall(
                r"<li\s+data-bdnum=['\"]([^'\"]+)['\"]\s+data-type=['\"]([^'\"]+)['\"][^>]*>\s*<a[^>]*>(.*?)</a>",
                result, re.DOTALL
            )
            return [{"bdnum": b, "type": t, "name": n.strip()} for b, t, n in facilities]
        except Exception:
            return []
        finally:
            browser.close()


# ─────────────────────────────────────────────
# 대화형 인터페이스
# ─────────────────────────────────────────────

def ask_date(prompt: str) -> str:
    while True:
        val = input(prompt).strip()
        try:
            datetime.strptime(val, "%Y-%m-%d")
            return val
        except ValueError:
            print("   날짜 형식이 올바르지 않습니다. (예: 2026-03-09)")


def ask_time(prompt: str) -> str:
    while True:
        val = input(prompt).strip()
        try:
            parse_time_range(val)
            return val
        except ValueError as e:
            print(f"   {e}")


def ask_numbered_choice(title: str, options: list[str]) -> int:
    """번호 목록 출력 후 선택 → 0-based index 반환"""
    print(f"\n  {title}")
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        val = input("\n  번호를 선택해주세요 : ").strip()
        if val.isdigit() and 1 <= int(val) <= len(options):
            return int(val) - 1
        print(f"   1 ~ {len(options)} 사이의 번호를 입력해주세요.")


def menu_reserve():
    print()

    # 1. 건물 선택
    site_names = list(SITE_IDS.keys())
    idx = ask_numbered_choice("1. 회의 건물을 선택해주세요", site_names)
    site = site_names[idx]
    scr_id = SITE_IDS[site]

    # 2. 시설 목록 조회 및 선택
    print("\n  시설 목록 조회 중...")
    facilities = get_facilities(scr_id)
    if not facilities:
        print("  ❌ 시설 목록을 불러올 수 없습니다.")
        return

    bookable = [f for f in facilities if f["type"] == "A"]
    inquiry  = [f for f in facilities if f["type"] != "A"]

    display_names = [f["name"] for f in bookable]
    if inquiry:
        display_names += [f"{f['name']} (전화문의)" for f in inquiry]

    fac_idx = ask_numbered_choice("2. 회의실을 선택해주세요", display_names)

    if fac_idx >= len(bookable):
        chosen = inquiry[fac_idx - len(bookable)]
        print(f"\n  ⚠️  '{chosen['name']}'은 온라인 예약이 불가하며 담당자에게 전화 문의가 필요합니다.")
        return

    facility = bookable[fac_idx]["name"]

    # 3. 날짜 입력
    date = ask_date("\n  3. 예약 날짜를 입력해주세요 (yyyy-mm-dd) : ")

    # 4. 시간 입력
    time_range = ask_time("  4. 예약 시간을 입력해주세요 (예: 09:00-10:30) : ")

    print()
    success = reserve(site=site, date=date, facility=facility, time_range=time_range)
    if not success:
        yn = input("\n  해당 날짜 현황을 조회하시겠습니까? (yes/no) : ").strip().lower()
        if yn in ("yes", "y"):
            menu_query(prefill_date=date)


def menu_cancel():
    print()
    print("  예약 목록 조회 중...")
    reservations = list_reservations()
    now = datetime.now()
    my_reservations = sorted(
        [
            r for r in reservations
            if r["is_mine"]
            and datetime.strptime(f"{r['date']} {r['end']}", "%Y-%m-%d %H:%M") > now
        ],
        key=lambda x: (x["date"], x["start"])
    )

    if not my_reservations:
        print("  취소할 예약이 없습니다.")
        return

    options = [
        f"{r['date']}  {r['start']} ~ {r['end']}  {r['facility_name']}"
        for r in my_reservations
    ]
    idx = ask_numbered_choice("1. 취소할 예약을 선택해주세요", options)

    r = my_reservations[idx]
    print(f"\n  선택: {r['date']}  {r['start']} ~ {r['end']}  {r['facility_name']}")
    confirm = input("  정말 취소하시겠습니까? (yes/no) : ").strip().lower()
    if confirm not in ("yes", "y"):
        print("  취소를 중단합니다.")
        return

    print()
    cancel_reservation(r["bd_num"])


def menu_query(prefill_date: str = None):
    print()
    if prefill_date:
        val = input(f"  조회 날짜 [{prefill_date}] (엔터 시 동일 날짜) : ").strip()
        try:
            date = val if val and datetime.strptime(val, "%Y-%m-%d") else prefill_date
        except ValueError:
            date = prefill_date
    else:
        date = ask_date("  1. 조회할 날짜를 입력해주세요 (yyyy-mm-dd) : ")

    print(f"\n  조회 중... ({date})")
    reservations = list_reservations(date=date)

    if not reservations:
        print("  해당 날짜에 예약 내역이 없습니다.")
        return

    from collections import defaultdict
    grouped = defaultdict(list)
    for r in reservations:
        grouped[r["facility_name"]].append(r)

    d = datetime.strptime(date, "%Y-%m-%d")
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    print(f"\n  📋 {d.year}년 {d.month}월 {d.day}일 ({weekdays[d.weekday()]}) 예약 현황\n")
    print(f"  {'회의실':<14} {'시간':<18} {'예약자':<18} {'예약번호'}")
    print("  " + "-" * 70)
    for fac_name in sorted(grouped):
        for r in sorted(grouped[fac_name], key=lambda x: x["start"]):
            mine_marker = " ◀" if r["is_mine"] else ""
            bd_num_display = r["bd_num"] if r["is_mine"] else "-"
            print(f"  {fac_name:<14} {r['start']} ~ {r['end']:<10} {r['company']:<18} {bd_num_display}{mine_marker}")


def main():
    print("=" * 50)
    print("  서울 AI 허브 회의실 예약봇")
    print("=" * 50)

    while True:
        print("""
  무슨 작업을 실행할까요?

  1. 회의실 예약
  2. 회의실 예약 취소
  3. 회의실 예약 및 예약번호 조회
  0. 종료
""")
        choice = input("  번호를 선택해주세요 : ").strip()

        if choice == "1":
            menu_reserve()
        elif choice == "2":
            menu_cancel()
        elif choice == "3":
            menu_query()
        elif choice == "0":
            print("\n  종료합니다.\n")
            break
        else:
            print("  1, 2, 3, 0 중 하나를 입력해주세요.")


if __name__ == "__main__":
    main()
