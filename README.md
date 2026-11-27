# 🛰️ BF Deal Radar — 해외직구 & 국내 핫딜 찐할인 알리미

해외직구(미국 중심)와 **국내 핫딜(쿠팡·네이버·G마켓·11번가·롯데온·SSG 등 전 품목)**을 실시간 동시 수집하고, **가짜 할인·포인트 적립 스팸·품절 딜을 거른 뒤 진짜 저렴한 딜만 텔레그램으로 알려주는** 경량 레이더입니다.

## ✨ 주요 기능

* **🇺🇸 해외직구 · 미국 블프/가격오류 · Apple/패션/가전/알리 26개 피드 동시 수집**
  * **Slickdeals 7개 전용 피드** (Frontpage / Popular / IT·PC / AliExpress·Tech / 패션·스니커즈 / Apple·OLED·오디오 / 다이슨·가전·리빙 — 커뮤니티 `Thumb Score` 추천수·쿠폰 코드 추출)
  * **DealNews 9개 전 카테고리** (Electronics / Computers / Home & Garden / Fashion & Apparel / Gaming & Toys / Sports & Fitness / Health & Beauty / Automotive & Tools / All — 스토어 배너 제외, 정가·`Staff Pick`·역대가 추출)
  * **Dan's Deals / GottaDeal / Hip2Save / 9to5Toys · 9to5Mac · 9to5Google / PCMag Deals / The Verge / CNET / IGN** (미국 블프·가격오류·Apple·IT·생활·식품·아마존 역대가 속보)
* **🇰🇷 국내 핫딜 29개 소스 · 주요 13대 쇼핑몰 전용관 & 10대 커뮤니티 통합 수집 (전 품목)**
  * **다사자 주요몰 전용관 (쿠팡 / 네이버 / G마켓 / 옥션 / 11번가 / 롯데온 / 토스쇼핑 / 오늘의집 / 마켓컬리 / 무신사 / 29CM / 올리브영 / 하이마트 / BEST / 국내 / 임박특가)** — 국내 주요 오픈마켓·패션·뷰티·리빙·식품·가전 실시간 특가 집중 수집
  * **다사자 해외·직구 특화 (알리 딜 / 테무 딜 / 해외 딜 / 아이허브 전날 대비 가격 하락)** — 중국 알리·테무·글로벌 직구 및 아이허브 실시간 가격 하락 추적
  * **핫구사 (실시간 핫딜 + 12개월 최저가 검증)** — 뽐뿌·에펨코리아·아카라이브·퀘이사존·쿨엔조이·어미새·루리웹·ZOD 8개 커뮤니티 통합 + **최근 12개월 평균가 대비 할인율(`lowestLabel`) 검증**
  * **어미새 (인기 / 패션 / 기타) / 루리웹 핫딜 / 클리앙 알뜰구매 / 딜바다 (국내핫딜 + 해외핫딜)** *(개드립 핫딜은 로컬 실행 시 `config.yaml`에서 활성화 가능)*
  * **엄격한 0~100점 다중 신호 채점(기본 65점 컷, 90점+ 희귀 대란, 100점 전설급)** + **품절/네이버페이 포인트 5원 스팸/성분%(연육 80% 등)/모음전 배너 과장 할인 자동 차단**
  * **📉 목표가 등록 없는 "기준가 대비 급락" 자동 감지 (2중 시세 검증)**
    * **자체 60일 KRW 시세 누적 (기본 자동 동작)**: 용량·수량·규격(256GB/2TB, 2L/24캔 등)을 보존한 상품 시그니처로 60일간 국내 시세를 자동 누적하고, 평소 시세 대비 급락 시 즉시 부스트 & 알림
    * *(선택)* **네이버 쇼핑 검색 API 실시간 시장 기준가 비교**: `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` 등록 시(무료 일 25,000건, 6시간 SQLite 캐싱) 처음 포착된 상품도 중고·액세서리·옵션 불일치를 거른 실시간 시세와 대조해 **시세 대비 30~45%+ 급락·가격오류 의심 딜을 즉시 포착**하고 **시세보다 비싼 가짜 할인은 자동 감점**
  * 원글에서 **실제 쇼핑몰 구매 직링크 자동 추출** → 텔레그램에 **`[🛒 바로 구매하기]`** + **`[💬 원글/댓글]`** + **`[🔍 네이버 최저가 비교]`** + **`[📊 다나와 시세]`** 원클릭 버튼 자동 첨부
* **한국인 맞춤 정보 & 보안**
  * 해외직구: 실시간 `USD → KRW` 환율 + **미국 목록통관 \$200 면세/관부가세 계산** + **110~120V 전압 주의** + **대형 배대지 배송비 경고** + **CCC/Keepa 차트 버튼**
  * 정적 웹 대시보드(`bf-radar dashboard`) & GitHub Pages 배포 워크플로우 포함
  * `ETag` / `If-Modified-Since` 조건부 HTTP 캐싱으로 5분 폴링 속도·안정성 최적화
  * *(선택)* `GEMINI_API_KEY` / `OPENAI_API_KEY` 추가 시 LLM 한국어 요약 & AI 검증 활성화

---

## 🚀 3분 만에 서버비 0원으로 시작하기 (GitHub Actions + 텔레그램)

Public 리포지토리는 GitHub Actions가 무료이므로 **별도 서버 없이 5분마다 자동 실행**됩니다.

### 1단계: 텔레그램 봇 생성 & Chat ID 확인
1. 텔레그램에서 **[@BotFather](https://t.me/BotFather)** 에게 `/newbot` 을 보내 봇을 만들고 **`TELEGRAM_BOT_TOKEN`** 을 복사합니다.
2. 방금 만든 봇 채팅방에 들어가 **시작(Start)** 버튼(또는 아무 메시지)을 한 번 보냅니다.
3. 본인 **`TELEGRAM_CHAT_ID`** 확인 (둘 중 편한 방법 선택):
   * **[@userinfobot](https://t.me/userinfobot)** 에게 메시지 보내서 숫자 `Id` 확인
   * 또는 로컬에서 `TELEGRAM_BOT_TOKEN=<토큰> bf-radar discover-chat-id` 실행해 자동 조회!

### 2단계: GitHub Secrets 등록
이 리포지토리의 **Settings → Secrets and variables → Actions → New repository secret** 에서 아래 값을 추가합니다.

| Secret 이름 | 설명 | 필수 여부 |
| :--- | :--- | :---: |
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급받은 봇 토큰 | **필수** |
| `TELEGRAM_CHAT_ID` | 알림을 받을 본인 Chat ID (또는 채널/그룹 ID) | **필수** |
| `NAVER_CLIENT_ID` | 네이버 개발자센터 쇼핑 검색 API Client ID ([무료 발급](https://developers.naver.com/apps/#/register), 일 25,000건) | 선택 |
| `NAVER_CLIENT_SECRET` | 네이버 개발자센터 쇼핑 검색 API Client Secret (국내 실시간 시세 비교용) | 선택 |
| `GEMINI_API_KEY` | Gemini 한국어 요약/검증용 키 ([Google AI Studio](https://aistudio.google.com/apikey)) | 선택 |
| `OPENAI_API_KEY` | OpenAI 호환 API 사용 시 | 선택 |

### 3단계: 동작 확인
* 리포지토리 상단 **Actions → Black Friday Deal Radar → Run workflow** 를 눌러 즉시 1회 테스트할 수 있습니다.
* 이후에는 **5분마다 자동으로** 새 핫딜을 수집해 텔레그램으로 발송합니다.

---

## 💻 로컬 / Docker / 웹 대시보드 실행

### Python 로컬 실행
```bash
pip install -e .
cp .env.example .env
# .env 파일에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 입력

# 1) 텔레그램 발송 없이 현재 찐할인 미리보기 (Dry Run)
bf-radar run --dry-run

# 2) 실시간 정적 웹 대시보드 생성 (docs/index.html + docs/deals.json)
bf-radar dashboard

# 3) 텔레그램 연결 테스트
bf-radar test-telegram

# 4) 2분(120초) 간격 상시 감시
bf-radar watch --interval 120
```

### Docker Compose 실행
```bash
cp .env.example .env
# .env 수정 후
docker compose up -d
```

---

## ⚙️ 관심 품목 & 기준 커스터마이징

[`config.yaml`](config.yaml) 에서 모든 기준을 손쉽게 조절할 수 있습니다.

* `radar.min_score` / `radar.kr_min_score`: 알림 최소 점수 (기본 **`65`**, `90+`는 희귀 초특가 대란만 도달)
* `filters.watch_keywords`: 여기에 넣은 키워드(예: `macbook`, `airpods`, `4tb`, `oled`, `sony`)는 가산점(+8~+10점) + 관심 키워드 배지 표시
* `filters.exclude_keywords` / `filters.kr_exclude_keywords`: 원치 않는 브랜드·카테고리·포인트 적립 스팸 제외

## 🧪 테스트 실행

```bash
pip install -e ".[dev]"
pytest -v
```

---

## 🔒 보안 가이드 (Public 리포지토리 주의사항)

이 리포지토리는 **Public**이므로 모든 코드·커밋·GitHub Actions 로그가 외부에 공개됩니다. 아래 보안 안전장치가 기본 적용되어 있습니다.

* **코드 레벨 시크릿 마스킹**:
  * `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `OPENAI_API_KEY`는 `repr=False`로 설정되어 객체 출력이나 트레이스백에 노출되지 않습니다.
  * 텔레그램 API 네트워크/HTTP 에러 발생 시에도 봇 토큰이 포함된 요청 URL이 로그에 찍히지 않도록 전역 `SecretRedactingFilter`와 에러 메시지 정제를 적용했습니다.
  * 원격 LLM API 엔드포인트(`OPENAI_BASE_URL`)는 `https://`만 허용하여 API 키가 평문 HTTP로 전송되는 것을 차단합니다.
* **GitHub Actions 최소 권한 & 인젝션 방지**:
  * 워크플로우는 읽기 전용 권한(`permissions: contents: read`)과 `persist-credentials: false`로 실행됩니다.
  * 시크릿은 `schedule` / `workflow_dispatch`에서만 주입되며 외부 PR(`pull_request`)에는 절대 전달되지 않습니다.
  * 캐시 DB(`data/radar.db`)에는 공개 핫딜 URL·가격 정보만 저장되며, 토큰·Chat ID·개인정보는 저장하지 않습니다.
* **직접 지키실 3가지 수칙**:
  1. 실제 키는 **절대 `config.yaml`이나 `.env.example`에 적지 말고**, 로컬은 `.env`(Git 무시됨), 자동화는 **GitHub Secrets**에만 등록하세요.
  2. 실수로 텔레그램 봇 토큰이 노출되었다면 즉시 **@BotFather → `/revoke`** 로 토큰을 재발급하세요.
  3. 리포지토리 **Settings → Code security**에서 **Secret scanning**과 **Push protection**을 켜두면 실수로 키를 커밋해도 GitHub가 푸시를 자동 차단해 줍니다.

