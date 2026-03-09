"""
서울 AI 허브 미팅룸 예약 자동화 — 진입점

실행:
  python reserve.py
"""
from datetime import datetime
from collections import defaultdict

from modules.config import SITE_IDS
from modules.book import reserve, get_facilities, parse_time_range
from modules.cancel import cancel_reservation
from modules.query import list_reservations


# ─────────────────────────────────────────────
# 입력 헬퍼
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


# ─────────────────────────────────────────────
# 메뉴
# ─────────────────────────────────────────────

def menu_reserve():
    print()

    site_names = list(SITE_IDS.keys())
    idx = ask_numbered_choice("1. 회의 건물을 선택해주세요", site_names)
    site = site_names[idx]
    scr_id = SITE_IDS[site]

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
    date = ask_date("\n  3. 예약 날짜를 입력해주세요 (yyyy-mm-dd) : ")
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


# ─────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────

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
