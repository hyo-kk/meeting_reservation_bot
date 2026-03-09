import re
import calendar as cal_module
from collections import defaultdict
from datetime import datetime, date as date_class, timedelta
from playwright.sync_api import sync_playwright

from .config import BASE_URL, SITE_IDS, COMPANY_NAME
from .auth import login


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
            if cur.weekday() < 5:
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

            all_facilities = {}
            for site_name, scr_id in SITE_IDS.items():
                for fac in _get_facilities_for_site(page, scr_id):
                    if fac["type"] == "A":
                        all_facilities[fac["bdnum"]] = {"name": fac["name"], "site": site_name}

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
