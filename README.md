# 🎯 Peter Lynch Screener V5

피터 린치 투자 전략 기반 미국 주식 스크리닝 봇 (GitHub Actions 자동화)

## ⚡ 핵심 기능

- **3중 검증**: Yahoo Finance + 직접 계산 + Finviz
- **공격적 포트폴리오**: 최고가치 40% + 고성장 40% + 균형 20%
- **GPT-4o 분석**: AI 기반 포트폴리오 추천
- **자동화**: GitHub Actions로 매주 월요일 자동 실행

## 🚀 빠른 시작

### 1. 저장소 생성 및 푸시

이 프로젝트는 `setup.sh` 스크립트로 자동 생성되었습니다.

```bash
# GitHub에서 새 저장소 생성 후
git remote add origin https://github.com/YOUR_USERNAME/peter-lynch-screener.git
git add .
git commit -m "Initial commit: Peter Lynch Screener V5 with GitHub Actions"
git push -u origin main
```

### 2. GitHub Secrets 설정

Repository → Settings → Secrets and variables → Actions → New repository secret

**필수:**
- `OPENAI_API_KEY`: OpenAI API 키

**선택 (Slack 알림):**
- `SLACK_BOT_TOKEN`: Slack Bot Token (xoxb-로 시작)
- `SLACK_CHANNEL_ID`: Slack Channel ID (C로 시작)

### 3. 자동 실행 확인

- **자동**: 매주 월요일 오전 9시 (UTC 기준 00:00)
- **수동**: Actions 탭 → "Peter Lynch Screener" → "Run workflow"

## 📊 결과 확인

1. **GitHub Actions**: Actions 탭 → 최신 workflow 클릭 → Artifacts 다운로드
2. **Slack**: 설정 시 자동으로 메시지 + 파일 전송

## 🔧 로컬 실행

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경 변수 설정
cp .env.example .env
# .env 파일 편집

# 실행
python peter_lynch_screener_v5.py
```

## 📁 파일 구조

```
peter-lynch-screener/
├── .github/
│   └── workflows/
│       └── screener.yml          # GitHub Actions 워크플로우
├── peter_lynch_screener_v5.py   # 메인 스크립트
├── requirements.txt              # Python 의존성
├── portfolio_history.json        # 포트폴리오 히스토리 (자동 생성)
├── .gitignore
├── .env.example
└── README.md
```

## �� 투자 전략

### 포트폴리오 구성
- **최고 가치주 (40%)**: PEG < 0.7, 성장률 20-50%
- **고성장주 (40%)**: 성장률 50%+, PEG < 1.2
- **균형 (20%)**: PEG < 1.0, 성장률 20-40%

### 진입 전략
- 1주차: 3%
- 2주차: 3%
- 3주차: 4%
- **총 10%** (1종목당)

## 📝 라이선스

MIT
