# main.py
# ─────────────────────────────────────────────────────────────
# KOBIS(영화진흥위원회) 일별 박스오피스 조회 앱
# - 조회 날짜: '한국 시간 기준 어제'를 매번 자동 계산
# - 인증키: Streamlit secrets 의 KOBIS_KEY 에서 읽어옴 (코드에 직접 쓰지 않음)
# ─────────────────────────────────────────────────────────────

import datetime as dt                 # 날짜 계산용 표준 라이브러리
from zoneinfo import ZoneInfo         # 시간대(타임존) 처리용 표준 라이브러리

import pandas as pd                   # 표(데이터프레임)를 다루는 라이브러리
import requests                       # 인터넷에 요청을 보내는 라이브러리
import streamlit as st                # 웹 화면을 만드는 라이브러리

# API 주소와 한국 시간대는 한 번 정해두고 계속 재사용합니다.
API_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"
KST = ZoneInfo("Asia/Seoul")          # 'Asia/Seoul' = 한국 표준시

# 페이지 기본 설정(제목, 아이콘, 넓은 레이아웃). 반드시 다른 st 명령보다 먼저 호출합니다.
st.set_page_config(page_title="어제의 박스오피스", page_icon="🎬", layout="wide")


# ─────────────────────────────────────────────────────────────
# 1) 날짜 계산: '어제'를 한국 시간 기준으로 구하기
# ─────────────────────────────────────────────────────────────
def get_yesterday_kst() -> str:
    """한국 시간 기준 '어제' 날짜를 'yyyymmdd' 여덟 자리 문자열로 돌려줍니다.

    배포 서버(스트림릿 클라우드)의 시계는 보통 UTC라서
    그냥 datetime.now() 를 쓰면 한국과 날짜가 하루 어긋날 수 있습니다.
    그래서 항상 KST(한국 시간)로 '지금'을 구한 뒤 하루를 뺍니다.
    """
    now_kst = dt.datetime.now(KST)            # 지금 시각(한국 기준)
    yesterday = now_kst - dt.timedelta(days=1)  # 하루 빼기
    return yesterday.strftime("%Y%m%d")       # 예: 20260916


def to_pretty_date(yyyymmdd: str) -> str:
    """'20260916' 같은 문자열을 '2026-09-16' 처럼 보기 좋게 바꿔 줍니다."""
    return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


# ─────────────────────────────────────────────────────────────
# 2) 문자열 숫자 → 진짜 숫자로 바꾸기
# ─────────────────────────────────────────────────────────────
def to_int(value) -> int:
    """KOBIS는 관객수·스크린수 등을 모두 '문자열'로 보냅니다.
    정렬과 그래프를 하려면 숫자로 바꿔야 해서 이 함수를 씁니다.
    값이 비어 있거나 이상하면 0으로 처리해 앱이 멈추지 않게 합니다.
    """
    try:
        # 혹시 '1,234' 처럼 쉼표가 섞여 와도 처리되도록 쉼표를 지웁니다.
        return int(str(value).replace(",", "").strip())
    except (ValueError, AttributeError, TypeError):
        return 0


# ─────────────────────────────────────────────────────────────
# 3) API 호출 (+ 1시간 캐시)
# ─────────────────────────────────────────────────────────────
# @st.cache_data 는 "같은 입력값으로 다시 부르면 실제 호출 없이 저장된 결과를 준다"는 뜻입니다.
# ttl=3600 은 저장한 결과를 3600초(=1시간) 동안만 쓰겠다는 의미입니다.
@st.cache_data(ttl=3600, show_spinner="박스오피스 정보를 불러오는 중입니다...")
def fetch_box_office(target_dt: str, api_key: str) -> dict:
    """박스오피스 데이터를 가져옵니다.

    ※ 여기서는 일부러 '예외를 던지지' 않고, 성공/실패를 딕셔너리로 돌려줍니다.
       그래야 실패한 결과까지 깔끔하게 다루고, 화면에서 안내 문구를 띄우기 쉽습니다.
       - 성공: {"ok": True, "movies": [...]}
       - 실패: {"ok": False, "reason": "...", "detail": "..."}
    """
    params = {"key": api_key, "targetDt": target_dt}

    try:
        response = requests.get(API_URL, params=params, timeout=10)  # 10초 안에 응답 없으면 포기
        response.raise_for_status()      # 404, 500 등 HTTP 오류면 예외 발생
        data = response.json()           # 응답 본문을 JSON(파이썬 딕셔너리)으로 변환
    except requests.exceptions.Timeout:
        return {"ok": False, "reason": "timeout"}
    except requests.exceptions.RequestException as error:
        return {"ok": False, "reason": "network", "detail": str(error)}
    except ValueError:
        # 200으로 응답은 왔는데 내용이 JSON이 아닌 경우(점검 안내 페이지 등)
        return {"ok": False, "reason": "not_json"}

    # ★ 중요: 인증키가 틀려도 상태코드는 200입니다.
    #    대신 응답 안에 faultInfo 라는 오류 상자가 들어옵니다.
    if "faultInfo" in data:
        fault = data.get("faultInfo", {}) or {}
        detail = f"{fault.get('errorCode', '')} {fault.get('message', '')}".strip()
        return {"ok": False, "reason": "fault", "detail": detail}

    # 정상 응답이면 boxOfficeResult > dailyBoxOfficeList 안에 영화 목록이 들어 있습니다.
    # .get(..., {}) 을 쓰면 해당 키가 없어도 오류 없이 빈 값으로 넘어갑니다.
    movies = data.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])

    if not movies:  # 목록이 아예 비어 있는 경우(너무 이른 시각, 잘못된 날짜 등)
        return {"ok": False, "reason": "empty"}

    return {"ok": True, "movies": movies}


# ─────────────────────────────────────────────────────────────
# 4) 받아온 목록 → 표(데이터프레임)로 정리
# ─────────────────────────────────────────────────────────────
def build_dataframe(movies: list) -> pd.DataFrame:
    """영화 목록(딕셔너리들의 리스트)을 숫자 변환까지 끝낸 표로 만듭니다."""
    rows = []
    for movie in movies:
        rows.append({
            "순위": to_int(movie.get("rank")),
            "영화명": movie.get("movieNm", "-"),
            "개봉일": movie.get("openDt", "-"),
            "관객수": to_int(movie.get("audiCnt")),
            "누적관객": to_int(movie.get("audiAcc")),
            "스크린수": to_int(movie.get("scrnCnt")),
            "상영횟수": to_int(movie.get("showCnt")),
            "순위증감": to_int(movie.get("rankInten")),
        })

    df = pd.DataFrame(rows)
    # 순위 오름차순(1위가 맨 위)으로 정렬합니다. reset_index 는 줄 번호를 0부터 다시 매깁니다.
    return df.sort_values("순위").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# 5) 오류가 났을 때 보여 줄 한국어 안내
# ─────────────────────────────────────────────────────────────
def show_error_help(result: dict, target_dt: str) -> None:
    """빈 화면 대신, 무엇을 확인하면 되는지 알려 줍니다."""
    reason = result.get("reason")
    detail = result.get("detail", "")

    if reason == "fault":
        st.error("인증키 문제로 보입니다. KOBIS가 오류 상자(faultInfo)를 보냈습니다.")
        st.markdown(
            """
            **이렇게 확인해 보세요**
            1. 스트림릿 클라우드 앱의 **Settings → Secrets** 에 `KOBIS_KEY` 가 저장돼 있는지 확인하세요.
            2. 키 값 앞뒤에 **따옴표·공백·줄바꿈**이 섞이지 않았는지 보세요.
            3. KOBIS 사이트에서 발급한 키가 **유효 기간이 지났거나 정지**되지 않았는지 확인하세요.
            4. 하루 호출 한도를 넘지 않았는지 확인하세요.
            """
        )
        if detail:
            st.caption(f"KOBIS가 알려 준 내용: {detail}")

    elif reason == "empty":
        st.warning(f"{to_pretty_date(target_dt)} 의 박스오피스 목록이 비어 있습니다.")
        st.markdown(
            """
            **이럴 때가 많습니다**
            1. 집계가 아직 끝나지 않은 시각입니다. 보통 **오전 중**에 전날 자료가 올라옵니다.
               잠시 뒤 아래 **새로고침** 버튼을 눌러 보세요.
            2. 조회 날짜가 **너무 과거이거나 미래**일 수 있습니다.
            3. KOBIS 쪽에서 일시적으로 자료를 내려놨을 수 있습니다.
            """
        )

    elif reason == "timeout":
        st.error("KOBIS 서버가 제때 응답하지 않았습니다(10초 초과).")
        st.markdown("잠시 뒤 아래 **새로고침** 버튼을 눌러 다시 시도해 보세요. "
                    "KOBIS 서버 점검 중일 수도 있습니다.")

    elif reason == "not_json":
        st.error("응답은 받았지만 정상적인 데이터 형식이 아닙니다.")
        st.markdown("KOBIS 서버 점검 안내 페이지가 대신 온 경우가 많습니다. "
                    "잠시 뒤 다시 시도해 보세요.")

    else:  # network 등 그 밖의 경우
        st.error("KOBIS 서버에 연결하지 못했습니다.")
        st.markdown(
            """
            **이렇게 확인해 보세요**
            1. 인터넷 연결 상태를 확인하세요.
            2. KOBIS 서버가 점검 중일 수 있으니 잠시 뒤 다시 시도해 보세요.
            """
        )
        if detail:
            st.caption(f"자세한 내용: {detail}")


# ─────────────────────────────────────────────────────────────
# 6) 실제 화면 그리기 (여기서부터 위에서 만든 함수들을 사용합니다)
# ─────────────────────────────────────────────────────────────
st.title("🎬 어제의 박스오피스")

target_dt = get_yesterday_kst()   # 한국 시간 기준 어제
st.caption(f"조회 기준일: **{to_pretty_date(target_dt)}** (한국 시간 기준 어제) · "
           "같은 날짜는 1시간 동안 저장된 결과를 사용합니다.")

# 인증키 읽어오기 — 없으면 앱을 더 진행하지 않고 안내만 보여 줍니다.
try:
    api_key = st.secrets["KOBIS_KEY"]
except (KeyError, FileNotFoundError):
    st.error("인증키(KOBIS_KEY)를 찾지 못했습니다.")
    st.markdown(
        """
        **이렇게 설정하세요**
        - 스트림릿 클라우드: 앱 화면 우측 상단 **Settings → Secrets** 에 아래처럼 넣고 저장하세요.
        - 내 컴퓨터에서 실행할 때: 프로젝트 폴더에 `.streamlit/secrets.toml` 파일을 만들어 같은 내용을 넣으세요.
        """
    )
    st.code('KOBIS_KEY = "여기에_발급받은_인증키"', language="toml")
    st.stop()   # 여기서 앱 실행을 멈춥니다.

# 새로고침 버튼: 저장해 둔 결과(캐시)를 지우고 다시 불러옵니다.
if st.button("🔄 새로고침 (저장된 결과 지우고 다시 불러오기)"):
    st.cache_data.clear()
    st.rerun()

# 데이터 가져오기
result = fetch_box_office(target_dt, api_key)

if not result["ok"]:
    show_error_help(result, target_dt)
    st.stop()   # 오류 안내만 보여 주고 종료

df = build_dataframe(result["movies"])

# ── 1위 영화: 지표 카드 세 장 ────────────────────────────────
top_movie = df.iloc[0]   # iloc[0] = 첫 번째 줄(=1위)

st.subheader(f"🥇 1위 · {top_movie['영화명']}")

col1, col2, col3 = st.columns(3)   # 화면을 3칸으로 나눕니다
col1.metric("관객수 (어제)", f"{top_movie['관객수']:,}명")   # :, 는 천 단위 쉼표
col2.metric("누적 관객수", f"{top_movie['누적관객']:,}명")
col3.metric("스크린 수", f"{top_movie['스크린수']:,}개")

st.caption(f"개봉일 {top_movie['개봉일']} · 상영횟수 {top_movie['상영횟수']:,}회 · "
           f"전날 대비 순위 증감 {top_movie['순위증감']:+d}")

st.divider()

# ── 관객수 상위 5편 막대그래프 ──────────────────────────────
st.subheader("📊 관객수 상위 5편")

# 관객수 기준 내림차순 정렬 후 위에서 5개만 선택합니다.
top5 = df.sort_values("관객수", ascending=False).head(5)

# set_index("영화명") 으로 영화명을 가로축 이름으로 씁니다.
st.bar_chart(top5.set_index("영화명")["관객수"])

st.divider()

# ── 전체 표 ────────────────────────────────────────────────
st.subheader("📋 전체 순위표")

# 요청한 열만 골라서 보여 줍니다. 숫자 열이라 표 머리글을 눌러 정렬할 수 있습니다.
table_df = df[["순위", "영화명", "개봉일", "관객수", "누적관객", "스크린수"]]

st.dataframe(
    table_df,
    hide_index=True,          # 왼쪽 줄 번호 숨기기
    use_container_width=True, # 화면 너비에 맞추기
    column_config={
        "순위": st.column_config.NumberColumn("순위", format="%d"),
        "관객수": st.column_config.NumberColumn("관객수", format="%d"),
        "누적관객": st.column_config.NumberColumn("누적관객", format="%d"),
        "스크린수": st.column_config.NumberColumn("스크린수", format="%d"),
    },
)

st.caption("자료 출처: 영화진흥위원회(KOBIS) 오픈 API")
