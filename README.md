# 🛰️ BF Deal Radar — 해외직구 블랙프라이데이 찐할인 알리미

해외(미국 중심) 핫딜 커뮤니티와 에디터 피드를 실시간 수집하고, **가짜 할인·미국 내수 전용 노이즈를 걸러낸 뒤 진짜 할인 폭이 큰 딜만 텔레그램으로 알려주는** 경량 레이더입니다.

## ✨ 주요 기능

* **빠른 해외직구 핫딜 수집 (공식 RSS/Atom 동시 수집)**
  * **Slickdeals Frontpage & Popular** (커뮤니티 `Thumb Score` 추천수 자동 추출)
  * **DealNews (Electronics / Computers / All)** (정가·에디터 `Staff Pick`·역대가 코멘트 추출)
  * **TechBargains** (아마존 직접 링크 & ASIN 추출)
  * **9to5Toys & The Verge Deals** (IT·Apple·가전 블랙프라이데이 역대가 속보)
  * *(로컬 실행 시 `config.yaml`에서 `Reddit r/buildapcsales` 추가 활성화 가능)*
* **다중 시그널 "찐할인" 검증 엔진 (0~100점)**
  * **할인율 정량 계산**: `% OFF`, `$X off`, `was $X` 자동 파싱
  * **역대가(All-Time Low) 감지**: 역대 최저가·연중 최저가 언급 및 자체 가격 이력 DB 대비 추가 하락 포착
  * **커뮤니티 검증**: Slickdeals Frontpage, 높은 추천수(`+50`, `+100`), DealNews Staff Pick 가산
  * **교차 사이트 검증**: 동일 상품(ASIN 또는 상품명 매칭)이 2개 이상 해외 사이트에 동시 등장 시 중복 병합 + 점수 대폭 가산
  * **해외직구 노이즈 자동 차단**: 미국 통신사(Verizon/AT&T), 배달앱(DoorDash), 오프라인 전용(`In-Store Only`), 스트리밍 번들 등 직구 불가 딜 자동 제외
* **한국인 해외직구 맞춤 정보**
  * 실시간 `USD → KRW` 환율 자동 환산
  * **미국 목록통관 \$200 면세/관부가세 초과 여부** 및 예상 총액 자동 계산
  * 아마존 상품은 **CamelCamelCamel / Keepa 가격 변동 차트 버튼**을 텔레그램 메시지에 바로 첨부
* *(선택)* **LLM 한국어 요약 & AI 검증**: `GEMINI_API_KEY` (무료 티어) 또는 `OPENAI_API_KEY`를 넣으면 한국어 제목·1줄 요약·직구 주의사항까지 자동 추가 (미설정 시에도 100% 무료 규칙 엔진으로 정상 동작)

---

## 🚀 3분 만에 서버비 0원으로 시작하기 (GitHub Actions + 텔레그램)

Public 리포지토리는 GitHub Actions가 무료이므로 **별도 서버 없이 5분마다 자동 실행**됩니다.

### 1단계: 텔레그램 봇 생성 & Chat ID 확인
1. 텔레그램에서 **[@BotFather](https://t.me/BotFather)** 에게 `/newbot` 을 보내 봇을 만들고 **`TELEGRAM_BOT_TOKEN`** 을 복사합니다.
2. 방금 만든 봇 채팅방에 들어가 **시작(Start)** 버튼을 한 번 누릅니다.
3. **[@userinfobot](https://t.me/userinfobot)** 에게 아무 메시지를 보내 본인의 숫자 **`Id`** (`TELEGRAM_CHAT_ID`)를 확인합니다.

### 2단계: GitHub Secrets 등록
이 리포지토리의 **Settings → Secrets and variables → Actions → New repository secret** 에서 아래 2개를 추가합니다.

| Secret 이름 | 설명 | 필수 여부 |
| :--- | :--- | :---: |
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급받은 봇 토큰 | **필수** |
| `TELEGRAM_CHAT_ID` | 알림을 받을 본인 Chat ID (또는 채널/그룹 ID) | **필수** |
| `GEMINI_API_KEY` | Gemini 한국어 요약/검증용 키 ([Google AI Studio](https://aistudio.google.com/apikey)) | 선택 |
| `OPENAI_API_KEY` | OpenAI 호환 API 사용 시 | 선택 |

### 3단계: 동작 확인
* 리포지토리 상단 **Actions → Black Friday Deal Radar → Run workflow** 를 눌러 즉시 1회 테스트할 수 있습니다.
* 이후에는 **5분마다 자동으로** 새 핫딜을 수집해 텔레그램으로 발송합니다.

---

## 💻 로컬 / Docker 실행 (1~2분 단위 초실시간 감시)

블랙프라이데이 초특가는 5분 안에 품절되기도 하므로, 더 빠른 폴링이 필요하면 Docker나 로컬에서 상시 실행할 수 있습니다.

### Python 로컬 실행
```bash
pip install -e .
cp .env.example .env
# .env 파일에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 입력

# 1) 텔레그램 발송 없이 현재 찐할인 미리보기 (Dry Run)
bf-radar run --dry-run

# 2) 텔레그램 연결 테스트
bf-radar test-telegram

# 3) 2분(120초) 간격 상시 감시
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

* `radar.min_score`: 알림 최소 점수 (기본 `60`, 찐역대가만 받고 싶으면 `70~75`로 상향)
* `filters.watch_keywords`: 여기에 넣은 키워드(예: `macbook`, `airpods`, `4tb`, `oled`, `sony`)는 **+24점 가산 + 최우선 알림**
* `filters.exclude_keywords`: 원치 않는 브랜드·카테고리 제외

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

