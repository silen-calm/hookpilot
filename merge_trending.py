#!/usr/bin/env python3
"""crawler 결과(trending_results_*.json)를 본업 카테고리 매칭 후 trending_videos.js로 자동 통합.

매일 crawler 실행 후 이 스크립트 호출 → trending_videos.js 갱신 → 페이지 새로고침 시 새 영상.
"""
import json, re, glob, os
from datetime import datetime
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))

# 본업 카테고리별 키워드 (title·channel·caption 텍스트 부분 매칭) — 카테고리당 100+ 변형
# 사장님 외주 시장 8업종, 각 업종 매일 100+ 영상 풀 유지 목표
CATEGORIES = {
    "beauty": [
        # 한국어 일반 (구체 키워드만 — "스킨" → "스킨케어"로 정확도 향상)
        "화장", "메이크업", "립스틱", "립글로스", "립밤", "쿠션", "마스카라", "스킨케어", "토너", "선크림", "헤어컷", "헤어염색", "네일아트", "향수", "코스메틱",
        "쿠숀", "글로우", "토너패드", "앰플", "세럼", "올인원", "안티에이징", "쿨톤", "웜톤", "민감성", "여드름",
        "각질", "수분", "보습", "미백", "주름", "탄력", "리프팅", "물광", "꿀광", "광채", "트리트먼트", "샴푸", "린스",
        "린서", "보디로션", "보디오일", "핸드크림", "립밤", "립스틱", "립틴트", "립글로스", "립라이너", "아이라이너",
        "아이섀도", "아이브로우", "마스크팩", "시트팩", "패드", "필링", "각질제거", "토닝", "리프트", "마스크",
        # 브랜드
        "올리브영", "올영", "아모레", "이니스프리", "에뛰드", "더페이스샵", "미샤", "헤라", "설화수", "라네즈", "라끄베르",
        "닥터자르트", "라로슈포제", "비오템", "에스티로더", "에스쁘아", "정샘물", "어뮤즈", "롬앤", "페리페라", "클리오",
        "이지듀", "원라이프", "넘버즈인", "셀퓨전씨", "코스알엑스", "cosrx", "anua", "round lab", "tirtir", "medicube",
        # 영문
        "makeup", "skincare", "kbeauty", "k-beauty", "perfume", "blush", "foundation", "lipstick", "glow", "tone",
        "lipgloss", "concealer", "mascara", "eyeliner", "eyeshadow", "blush", "highlighter", "contour", "primer",
        "moisturizer", "serum", "essence", "toner", "cleanser", "sunscreen", "spf", "ampoule", "retinol", "niacinamide",
        "hyaluronic", "vitamin c", "glow recipe", "haus labs", "rare beauty", "fenty", "charlotte tilbury",
        # 헤어·바디 추가 (사장님 본업 매칭 누락 fix)
        "hair", "haircare", "헤어", "헤어에센스", "헤어팩", "헤어트리트먼트", "샴푸", "린스",
        "두피", "탈모", "탈모예방", "헤어컬러", "염색약", "고데기",
        "바디케어", "바디로션", "바디스크럽", "데오드란트",
    ],
    "d2c": [
        "무신사", "29cm", "29씨엠", "쿠팡", "스마트스토어", "네이버스토어", "지그재그", "에이블리", "브랜디", "스타일쉐어",
        "lookfantastic", "케이쇼핑", "ssg", "cj온스타일", "롯데온", "옷", "옷차림", "옷잘입는법",
        "코디", "패션", "원피스", "스타일", "haul", "ootd", "outfit", "fashion", "스니커즈", "운동화", "백", "가방",
        "악세사리", "주얼리", "데님", "셋업", "아우터", "캐주얼", "여름룩", "겨울룩", "봄룩", "가을룩", "데일리룩",
        "오피스룩", "데이트룩", "여행룩", "스트리트", "미니멀", "워크웨어", "워크룩", "캠퍼스룩", "캠핑룩",
        "지오다노", "유니클로", "uniqlo", "스파오", "spao", "탑텐", "topten", "에잇세컨즈", "8seconds", "후아유", "whoau",
        "푸마", "puma", "나이키", "nike", "아디다스", "adidas", "뉴발란스", "new balance", "컨버스", "converse",
        "반스", "vans", "리바이스", "levis", "노티카", "nautica", "지프", "jeep", "타미", "tommy", "ralph lauren",
        "guess", "calvin klein", "스타일링", "데일리", "남자코디", "여자코디", "직장인룩", "남자룩북", "여자룩북",
        "린넨", "셔츠", "블라우스", "맨투맨", "후드", "후드티", "니트", "가디건", "코트", "패딩", "자켓", "트렌치코트",
        "치마", "스커트", "팬츠", "슬랙스", "와이드팬츠", "스키니진", "부츠컷", "조거", "트레이닝", "백팩", "토트백",
    ],
    "med": [
        # 일반 단어 (건강·통증·치료·후기·이마·턱·침 등) 제거 — 음식/일반 영상에 광범위 매칭
        "의사", "치과의사", "피부과", "원장님", "성형외과", "닥터", "한의원", "약사", "한의사",
        "임플란트", "라식", "탈모치료", "여드름치료", "모공축소", "리프팅", "보톡스", "필러", "도수치료",
        "체형교정", "비포애프터", "치아미백", "흉터치료", "메디컬", "성형수술", "쌍수", "코수술", "지방흡입", "안면윤곽",
        "스킨부스터", "리쥬란", "히알루론", "프락셀", "ipl 레이저", "보톡사", "위고비", "삭센다", "마운자로",
        "추나요법", "물리치료사", "약침치료", "건강검진", "영양제 추천", "비타민 추천",
        "고혈압 약", "당뇨 관리", "갑상선 약", "콜레스테롤",
        "dermatologist", "dentist", "dermatology clinic", "skin clinic", "hair clinic", "botox", "lip filler", "implant procedure",
        "lasik surgery", "plastic surgery", "rhinoplasty", "liposuction", "blepharoplasty", "orthopedic",
        "오스템임플란트", "유디치과", "강남세브란스", "강남성심", "분당서울대",
        "수술후기", "임플란트후기", "성형후기", "쌍수후기", "코수술후기", "지방흡입후기", "리프팅후기",
    ],
    "pet": [
        "강아지", "고양이", "반려", "수의사", "동물병원", "댕댕이", "냥이", "사료", "간식", "훈련사", "강형욱", "수제간식",
        "예방접종", "중성화", "펫미용", "펫호텔", "유기견", "말티즈", "포메", "치와와", "비숑", "푸들", "시바견", "리트리버",
        "보더콜리", "허스키", "닥스훈트", "퍼그", "코카스파니엘", "포메라니안", "스피츠", "이비잔", "셔틀랜드", "사모예드",
        "골든리트리버", "래브라도", "프렌치불독", "불독", "치와와", "요크셔테리어", "비글", "콜리", "달마시안", "셰퍼드",
        "냥스타", "냥스타그램", "멍스타", "멍스타그램", "고양이일상", "강아지일상", "반려동물", "반려묘", "반려견",
        "수의학", "예방접종", "광견병", "심장사상충", "벼룩", "진드기", "그루밍", "미용", "펫숍", "사료추천", "간식추천",
        "삑삑이", "장난감", "캣타워", "캣휠", "고양이장난감", "강아지장난감", "산책", "도그파크", "리드줄", "하네스",
        "puppy", "kitten", "dog", "cat", "pet", "doglovers", "petlovers", "doglover", "catlover", "dogsofinstagram",
        "catsofinstagram", "petsofinstagram", "veterinarian", "veterinary", "vet", "petfood", "pettreats",
        "petgrooming", "dogtraining", "cattraining", "puppylove", "kittenlove",
    ],
    "smb": [
        "카페", "식당", "헤어샵", "미용실", "공방", "소상공인", "자영업", "사장님", "매장", "오픈", "창업", "매출공개",
        "점주", "프랜차이즈", "1인사장", "권리금", "상권", "네이버플레이스", "배민광고", "포스기", "월매출", "일매출",
        "카페창업", "식당창업", "분식집", "치킨집", "삼겹살집", "고기집", "분식", "포차", "이자카야", "오마카세",
        "와인바", "바", "펍", "주점", "회식", "단체손님", "단체석", "회식추천", "팀회식", "직장인회식", "워크샵",
        "공유오피스", "공방창업", "원룸창업", "스튜디오창업", "베이커리창업", "디저트카페", "라떼아트", "바리스타",
        "원두", "에스프레소", "드립", "콜드브루", "더치커피", "더치", "필터커피", "원두로스팅", "원두납품",
        "장사", "장사잘되는법", "장사꿀팁", "장사고수", "매출올리는법", "재방문률", "고정고객", "단골", "단골만들기",
        "리뷰관리", "리뷰이벤트", "후기이벤트", "사진이벤트", "스토어팜", "스토어로얄티", "포인트", "마일리지",
        "기프티콘", "쿠폰", "프로모션", "할인행사", "타임세일", "오픈이벤트", "신메뉴", "메뉴개발", "원가율",
        "store", "shop", "small business", "smallbiz", "smallbusiness", "entrepreneur", "boss",
        "프랜차이즈본사", "가맹", "가맹점", "가맹상담", "본사상담", "본부", "본사", "지사", "지사장", "지점",
    ],
    "food": [
        # 디저트·카페·vlog 누락 fix
        "dessert", "디저트", "카페투어", "동네카페", "감성카페", "케이크", "마카롱", "쿠키",
        "먹방", "맛집", "요리", "배달", "레시피", "쿠킹", "마라탕", "디저트", "쿠키", "케이크", "라면", "치킨", "피자", "버거",
        "먹스타", "foodie", "베이커리", "브런치", "파스타", "한식", "분식", "족발", "밀키트", "혼밥", "asmr먹방", "모카번",
        "크로플", "와플", "팬케이크", "토스트", "샌드위치", "햄버거", "샐러드", "스테이크", "초밥", "회", "초밥집", "회집",
        "일식집", "양식집", "중식집", "한식집", "한정식", "백반", "찌개", "국밥", "탕", "전골", "찜", "구이", "볶음",
        "양념치킨", "후라이드", "닭강정", "보쌈", "감자탕", "곱창", "막창", "대창", "삼겹살", "오겹살", "스테이크",
        "마라샹궈", "탕수육", "짜장면", "짬뽕", "볶음밥", "덮밥", "비빔밥", "김치찌개", "된장찌개", "순두부찌개",
        "콩나물국밥", "설렁탕", "곰탕", "해장국", "라멘", "우동", "소바", "돈까스", "돈가스", "카츠", "카레",
        "딸기", "포도", "수박", "복숭아", "참외", "사과", "배", "오렌지", "레몬", "라임", "파인애플", "망고",
        "초콜릿", "사탕", "마카롱", "베이글", "도넛", "도넛집", "도너츠", "크림빵", "단팥빵", "소금빵",
        "starbucks", "mcdonalds", "ice cream", "coffee", "drink", "cooking", "snack", "chocolate", "mukbang",
        "food", "recipe", "homemade", "asmr food", "asmr cooking", "asmr eating", "yummy", "delicious",
        "tasty", "foodporn", "foodgasm", "foodlover", "foodaddict", "foodvideo", "foodphotography",
    ],
    "fitness": [
        "헬스", "다이어트", "운동", "필라테스", "요가", "홈트", "근육", "복근", "유산소", "스쿼트", "데드리프트",
        "벤치프레스", "체지방", "체중감량", "복근운동", "코어운동", "코어", "필라테스기구", "리포머", "캐딜락", "체어",
        "맨몸운동", "맨몸", "푸시업", "풀업", "친업", "딥스", "런지", "버피", "버피테스트", "마라톤", "러닝", "달리기",
        "조깅", "걷기", "트레드밀", "사이클", "스피닝", "수영", "수영장", "프리스타일", "배영", "평영", "접영",
        "복싱", "킥복싱", "주짓수", "유도", "태권도", "합기도", "검도", "테니스", "배드민턴", "탁구", "골프 레슨",
        "골프스윙", "골프자세", "스포츠웨어", "젝시믹스", "안다르", "뮬라웨어", "lululemon", "athleta", "alo yoga",
        "workout", "fitness", "gym", "diet", "abs", "exercise", "stretching", "yoga", "pilates", "crossfit",
        "weightloss", "weighttraining", "bodybuilding", "powerlifting", "calisthenics", "hiit", "cardio",
        "stretching", "flexibility", "mobility", "recovery", "hyrox", "spartan",
        "personal trainer", "pt", "트레이너", "코치", "강사", "필라테스강사", "요가강사", "운동강사",
    ],
    "edu": [
        "공부", "강의", "학원", "수능", "토익", "영어", "수학", "공시", "교육", "토플", "면접", "자격증", "공인중개사", "한국사",
        "수능국어", "수능수학", "수능영어", "수능과탐", "수능사탐", "탐구", "내신", "내신대비", "모의고사", "모고", "교육과정",
        "수능공부법", "공부법", "공부자극", "공부일상", "공부영상", "공부유튜브", "공시생", "공무원", "9급", "7급",
        "행정고시", "외무고시", "변호사시험", "변시", "사법시험", "회계사", "세무사", "감정평가사", "노무사",
        "변리사", "관세사", "법무사", "공인노무사", "공인회계사", "한자급수", "한자검정", "한국사능력검정",
        "한국사능력시험", "한능검", "한자", "한자공부", "오픽", "오픽시험", "텝스", "토익스피킹", "토익라이팅",
        "study", "school", "university", "tutor", "studygram", "studyblr", "studywithme", "studymotivation",
        "studytips", "studyhard", "examprep", "examstudy", "gre", "gmat", "sat", "ielts", "toefl",
        "online learning", "online course", "elearning", "edtech", "tutoring", "private tutor", "english tutor",
        "math tutor", "korean tutor", "language exchange", "language learning", "polyglot",
        "메가스터디", "이투스", "대성", "에듀윌", "해커스", "ybm", "윤선생", "시원스쿨", "민병철", "에듀와이즈",
    ],
}

# 제외 키워드 — 사장님 외주에 쓸 수 없는 카테고리만 확실히 (K-pop·게임·뉴스·B2B·스포츠)
# 너무 일반적인 단어(도전·리액션·vlog·유튜버 등)는 제외 X — 사장님 영상도 종종 사용
EXCLUDE_KEYWORDS = [
    # K-pop·연예 (확실)
    "bts ", "blackpink", "newjeans", "ive ", "le sserafim", "aespa", "twice ", "stray kids",
    "kpop", "k-pop", "케이팝", "아이돌", "idol ", "comeback", "데뷔무대", "음방", "엠카",
    # 게임·웹툰 (확실)
    "lol ", "리그오브레전드", "오버워치", "발로란트", "valorant", "fortnite",
    "마인크래프트", "minecraft", "로블록스", "roblox", "원펀맨",
    "웹툰", "anime ", "코스프레", "cosplay",
    # 스포츠·격투 (확실)
    "축구", "야구", "농구", "골프 경기", "ufc", "올림픽", "월드컵",
    "soccer", "baseball", "basketball ",
    # 뉴스·정치 (확실)
    "속보", "정치", "대통령", "선거", "국회", "breaking news",
    # B2B·기업 광고
    "b2b", "기업소개", "ipo", "투자유치", "saas플랫폼", "솔루션 소개",
    # 명백히 사장님 외주 무관
    "몰카", "프랭크 ", "prank",
    # 글로벌 viral 광고 — 사장님 외주 클라이언트가 글로벌 브랜드 아니라서 본업과 거리 멀음
    "adidas", "nike", "puma", "underarmour", "under armour", "newbalance", "new balance",
    "reebok", "asics", "fila",
    "juventus", "real madrid", "barcelona", "manchester", "liverpool", "psg", "bayern",
    "jude bellingham", "messi", "ronaldo", "mbappe", "vinicius",
    "world cup", "champions league", "premier league",
    # 자동차 글로벌 브랜드 광고
    "lamborghini", "ferrari", "porsche", "bmw 광고", "mercedes 광고", "audi 광고",
    "tesla 광고", "rolls royce",
    # 글로벌 luxury (사장님 본업 외주 시장 X)
    "chanel", "gucci", "louis vuitton", "hermes", "dior", "prada", "balenciaga",
    "rolex", "patek philippe",
    # 글로벌 entertainment·movie
    "marvel", "disney", "pixar", "netflix original", "hbo",
]

def is_excluded(v):
    """제외 키워드 매칭 시 True — 풀에서 제거."""
    text = ((v.get("title") or "") + " " + (v.get("channel") or "")).lower()
    return any(kw.lower() in text for kw in EXCLUDE_KEYWORDS)

# 쇼핑 영상 = 진짜 매출 직결 신호 있는 것만. 단순 추천·리뷰는 일반 풀에.
# 6 카테고리 — 1) 구매 link  2) 할인 코드  3) 명확 CTA  4) 가격+마감  5) 매장 위치  6) 협찬·광고 명시
SHOP_SIGNALS = {
    # 1. 구매 링크·바이오 링크 — 가장 강한 매출 신호
    "buy_link": [
        "linkinbio", "linkbio", "link in bio", "bio link", "shop my", "shopnow", "shop now",
        "smartstore", "스마트스토어", "쿠팡파트너스", "coupang", "쿠팡", "올영", "올리브영",
        "amazon finds", "amazonfinds", "ltk", "shopltk",
    ],
    # 2. 할인 코드 — 명확한 매출 추적 신호
    "discount_code": [
        "promo code", "프로모코드", "할인코드", "쿠폰코드", "code:", "코드:", "use code",
        "discount code", "10% off", "20% off", "30% off",
    ],
    # 3. 명확한 행동 유도 — 구매까지 끌고감
    "direct_cta": [
        "여기서 사세요", "여기서 구매", "여기 클릭", "지금 구매", "구매처",
        "바로 구매", "구매 링크", "shop here", "buy now", "order now",
    ],
    # 4. 가격 + 마감 — 한정·세일 매출 push
    "deal_urgency": [
        "마감임박", "오픈런", "오늘만", "한정수량", "단 0", "단 1", "단 2", "단 3", "단 4", "단 5",
        "flash sale", "limited time", "마감 직전", "한정판",
    ],
    # 5. 매장 위치 정보 — offline 매출 직결 (맛집·매장)
    "store_location": [
        "📍", "주소:", "오시는 길", "찾아오시는 길", "매장 위치", "위치 :",
    ],
    # 6. 협찬·광고 명시 — 광고 영상은 매출 견인 의도 명확
    "sponsored": [
        "#광고", "#협찬", " ad ", "#ad", "sponsored", "유료광고", "광고포함",
        "협찬받음", "협찬o", "내돈내산", "내돈산",
    ],
}
# 평탄화 — 매칭용
SHOP_KEYWORDS = [kw for kws in SHOP_SIGNALS.values() for kw in kws]

def classify_track(v):
    """쇼핑 영상이면 'shop' (진짜 매출 신호 있는 영상만), 아니면 'general'.
    단순 '추천'·'리뷰' 같은 일반 단어 빼고, 구매 link·할인 코드·명확 CTA·매장 위치·협찬 명시만.
    """
    text = " ".join([
        v.get("title") or "", v.get("channel") or "",
        v.get("caption") or "", v.get("description") or "",
        v.get("category") or "",
    ]).lower()
    return "shop" if any(k.lower() in text for k in SHOP_KEYWORDS) else "general"

def classify(v):
    # title + channel + caption + tags + category 모두 매칭 — 매칭률 끌어올림
    text = " ".join([
        v.get("title") or "", v.get("channel") or "", v.get("tiktokUser") or "",
        v.get("caption") or "", v.get("description") or "", v.get("category") or "",
        " ".join(v.get("tags") or []), " ".join(v.get("categories") or []),
    ]).lower()
    for ind, kws in CATEGORIES.items():
        if any(k.lower() in text for k in kws):
            return ind
    # Reddit IG 등 외부 큐레이션 — category_hint (서브레딧 카테고리) → 본업 매핑
    hint = v.get("category_hint")
    if hint:
        HINT_MAP = {
            "beauty": "beauty", "fashion": "d2c", "medical": "med",
            "pet": "pet", "fnb": "food", "fitness": "fitness",
            "smb": "smb", "education": "edu",
        }
        return HINT_MAP.get(hint)
    return None

def days_ago(uploadDate):
    if not uploadDate: return None
    try:
        d = datetime.strptime(uploadDate, "%Y-%m-%d")
        return max(0, (datetime.now() - d).days)
    except: return None

def merge():
    # === 옛 풀 영원히 누적 보존 시스템 ===
    # 사장님 호소: "매일 비슷한 영상 개수, 누적 안 됨"
    # 진짜 원인: 매일 매칭 룰 재적용 → 같은 영상만 통과
    # 해결: 옛 trending_videos.js 의 영상 ID 영원히 보존 (시간 컷·views 컷 만 적용)
    #       새 trending_results 의 새 영상만 본업 매칭 적용 + 풀에 추가
    existing_pool_ids = set()  # 옛 풀 영원 보존 ID 집합
    existing_pool = []
    old_pool_path = os.path.join(ROOT, "trending_videos.js")
    if os.path.exists(old_pool_path):
        try:
            with open(old_pool_path, encoding="utf-8") as f: old_txt = f.read()
            m = re.search(r"window\.TRENDING_VIDEOS\s*=\s*(\[.*?\]);", old_txt, re.DOTALL)
            if m:
                existing_pool = json.loads(m.group(1))
                for v in existing_pool:
                    k = v.get("tiktokId") or v.get("youtubeId") or v.get("instagramShortcode")
                    if k: existing_pool_ids.add(k)
                print(f"[merge] 옛 풀 보존 {len(existing_pool)}개 (사장님 누적 요구)")
        except Exception as e:
            print(f"[merge] 옛 풀 로드 skip: {e}")

    # 모든 trending_results_*.json 합치기 (오래된 것부터 → first_seen 보존)
    files = sorted(glob.glob(os.path.join(ROOT, "trending_results_*.json")))
    print(f"[merge] {len(files)}개 결과 파일 발견")
    combined = []
    for f in files:
        try:
            d = json.load(open(f))
            # 파일명에서 YYYY-MM-DD 추출 → 각 영상에 _first_seen 부여
            fname = os.path.basename(f)
            mdate = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
            first_seen = mdate.group(1) if mdate else None
            for vid in d.get("videos", []):
                if first_seen and "_first_seen" not in vid:
                    vid["_first_seen"] = first_seen
            combined.extend(d.get("videos", []))
        except Exception as e:
            print(f"[merge] {f} skip: {e}")
    print(f"[merge] 합친 영상: {len(combined)}")

    # dedupe — 1차: 플랫폼 ID (앞이 가장 오래된 → 보존하면 first_seen 정확)
    seen = set()
    unique = []
    for v in combined:
        k = v.get("tiktokId") or v.get("youtubeId") or v.get("instagramShortcode")
        if k and k not in seen:
            seen.add(k)
            unique.append(v)
        elif not k:
            unique.append(v)
    print(f"[merge] ID dedupe 후: {len(unique)}")

    # dedupe — 2차: 제목 정규화 (이모지·해시태그·공백 제거 후 첫 60자) 동일하면 크로스 플랫폼 중복으로 간주
    def _norm(t):
        if not t: return ""
        t = re.sub(r"[#@][\w가-힣]+", " ", t.lower())
        t = re.sub(r"[^\w가-힣\s]", " ", t)
        return re.sub(r"\s+", " ", t).strip()[:60]
    seen_norm = set()
    unique2 = []
    for v in unique:
        n = _norm(v.get("title"))
        if len(n) >= 8 and n in seen_norm:
            continue
        if len(n) >= 8: seen_norm.add(n)
        unique2.append(v)
    unique = unique2
    print(f"[merge] 제목 유사도 dedupe 후: {len(unique)}")

    # 본업 카테고리 매칭 + 1년 컷오프 + 시간대별 조회수 컷오프 + 제외 카테고리
    # 조회수 컷오프 차등 — 매칭률 우선, 너무 가혹하지 않게 완화:
    #   - 30일 이내: 3,000 (신선도 보호 — 막 올린 영상도 포함)
    #   - 30~90일: 5,000
    #   - 90~180일: 10,000
    #   - 180~365일: 20,000
    def min_views_for(days):
        # 사용자 요청 2000+ 매일 / 시간 흐를수록 단계별 완화
        if days is None: return 2000
        if days <= 30: return 2000
        if days <= 90: return 3000
        if days <= 180: return 5000
        return 10000

    skipped_excluded = 0
    skipped_views = 0
    skipped_old = 0
    skipped_empty_title = 0
    next_id = 10000
    out = []
    for v in unique:
        # 0차: 제목 비어 있는 영상 — 매출 분석·후킹 분류 불가능 → 풀 제외
        # (사용자 화면에 "(제목 분석 중)" 빈 카드 노출 차단)
        _t = (v.get("title") or "").strip()
        if not _t:
            skipped_empty_title += 1
            continue
        # 1차: 제외 카테고리 (K-pop·게임·뉴스·B2B 등)
        if is_excluded(v):
            skipped_excluded += 1
            continue
        ind = classify(v)
        if not ind: continue
        days = days_ago(v.get("uploadDate"))
        # 시간 컷 365 → 730일 (사장님 누적 가속 — 옛 영상 더 보존)
        if days is not None and days > 730:
            skipped_old += 1
            continue
        # 선정 기준 컷오프 — 시간대별 차등
        views = v.get("views")
        likes = v.get("likes")
        min_v = min_views_for(days)
        # views 정보 없는 영상 처음부터 제외 — 사용자가 100·300회 노출 안 보게 (근본 픽스)
        # 단 IG Reels(DDG/Bing 큐레이션) + YT RSS는 무료 자동 수집에서 views 메타 X가 본질
        src = v.get("_source") or ""
        is_ig = v.get("platform") == "Reels"
        is_curated_ig = "websearch" in src or "ddg_search" in src or "bing_search" in src or "reddit_search" in src
        is_yt_rss = "yt_rss" in src
        is_scrapetube = "scrapetube" in src  # YT scrapetube 결과는 views가 있지만 None일 경우도 허용
        if views is None and not ((is_ig and is_curated_ig) or is_yt_rss or is_scrapetube):
            skipped_views += 1
            continue
        if views is not None and views < min_v:
            skipped_views += 1
            continue
        if likes is not None and likes < 20 and "ytdlp_search" not in (v.get("_source") or ""):
            continue
        item = {
            "id": next_id, "country": v.get("country", "US"),
            "platform": v.get("platform"),
            # title 200자 초과 영상 (10%) 카드 화면 깨짐 회귀 — 150자 cap (대부분 TikTok 영상 hashtag 폭주)
            "title": (v.get("title", "") or "")[:150],
            "channel": v.get("channel", ""), "industry": ind,
            "track": classify_track(v), "hook": "result",
            # publishedDaysAgo가 없으면 풀 입성일(firstSeen) 기반으로 계산 → 30 기본값 박혀 차이 사라지는 문제 해결.
            "publishedDaysAgo": (
                days if days is not None
                else (
                    max(0, (datetime.now() - datetime.strptime(v.get("_first_seen"), "%Y-%m-%d")).days)
                    if v.get("_first_seen") else 30
                )
            ),
            "views": v.get("views"), "likes": v.get("likes"),
            "firstSeen": v.get("_first_seen"),  # YYYY-MM-DD — 풀에 처음 들어온 날
        }
        # 플랫폼별 id 필드
        if v.get("tiktokId"):
            item["tiktokId"] = v["tiktokId"]
            item["tiktokUser"] = v.get("tiktokUser", "")
        if v.get("youtubeId"):
            item["youtubeId"] = v["youtubeId"]
        if v.get("instagramShortcode"):
            item["instagramShortcode"] = v["instagramShortcode"]
        out.append(item)
        next_id += 1

    print(f"[merge] 제외 카테고리 차감: {skipped_excluded} / 시간 컷 차감: {skipped_old} / 조회수 컷 차감: {skipped_views} / 제목 빈 영상 차감: {skipped_empty_title}")
    print(f"[merge] 본업 매칭 (이번 cron): {len(out)}")
    # === 옛 풀 영영 보존 — 사장님 누적 요구 ===
    # 옛 풀에 있던 영상 중 ID 가 새 매칭 결과에 없으면 → 옛 그대로 보존 (시간 컷 365일 만 적용)
    new_ids = set()
    for v in out:
        k = v.get("tiktokId") or v.get("youtubeId") or v.get("instagramShortcode")
        if k: new_ids.add(k)
    preserved = 0
    today = datetime.now()
    for old_v in existing_pool:
        k = old_v.get("tiktokId") or old_v.get("youtubeId") or old_v.get("instagramShortcode")
        if not k or k in new_ids: continue  # 새 결과에 이미 있음 — 새 데이터로 갱신
        # 365일 컷오프 — uploadDate 없으면 firstSeen 기준
        days = old_v.get("publishedDaysAgo", 0)
        if isinstance(days, (int, float)) and days > 730:  # 365 → 730 (누적 가속)
            continue
        # 옛 풀 영상 ID 새 id 재부여 (충돌 방지)
        old_v["id"] = next_id; next_id += 1
        out.append(old_v)
        preserved += 1
    print(f"[merge] 옛 풀 누적 보존: {preserved}개 — 사장님 매일 누적 요구 충족")
    print(f"[merge] 최종 풀: {len(out)}")
    print("[merge] 업종 분포:", dict(Counter(x["industry"] for x in out)))
    print("[merge] 플랫폼 분포:", dict(Counter(x["platform"] for x in out)))

    # trending_videos.js 갱신
    out_path = os.path.join(ROOT, "trending_videos.js")
    iso_now = datetime.now().isoformat(timespec="seconds")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"// crawler 결과 자동 통합 — {iso_now}\n")
        f.write(f"// 총 {len(out)}개 (본업 매칭 + 1년 이내)\n")
        f.write("window.TRENDING_VIDEOS = " + json.dumps(out, ensure_ascii=False, indent=1) + ";\n")
        f.write(f"window.TRENDING_LAST_UPDATE = {json.dumps(iso_now)};\n")
    print(f"[merge] trending_videos.js 갱신 ({len(out)}개, last_update={iso_now})")

if __name__ == "__main__":
    merge()
