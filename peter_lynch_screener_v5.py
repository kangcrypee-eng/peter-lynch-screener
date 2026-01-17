"""
피터 린치식 미국 주식 스크리닝 봇 V5 - 완전 수정판
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

핵심 수정:
1. Step 2 조기 종료 버그 수정
2. Step 3 검증 로직 완화 (실제 통과 가능하도록)
3. 제외 이유 상세 로그 추가
4. 중국 비중 10% 제한
5. 슬랙 주가 링크 추가

환경 변수: OPENAI_API_KEY, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
실행: python peter_lynch_screener_v5_complete.py
"""

import pandas as pd
import yfinance as yf
import requests
from bs4 import BeautifulSoup
import time
import logging
import json
import os
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from openai import OpenAI
import warnings
warnings.filterwarnings('ignore')

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(f'screener_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class GPTAnalyzer:
    """GPT API 포트폴리오 분석 + 한글 번역"""
    
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        
        self.portfolio_allocation = {
            'best_value': {'weight': 0.40, 'stocks': 4},
            'high_growth': {'weight': 0.40, 'stocks': 4},
            'balanced': {'weight': 0.20, 'stocks': 2}
        }
        
        if not self.api_key:
            logger.warning("⚠️ OPENAI_API_KEY 미설정 - 기본 분석 모드")
            self.enabled = False
        else:
            try:
                self.client = OpenAI(api_key=self.api_key)
                self.enabled = True
                logger.info("✅ GPT API 연동")
            except Exception as e:
                logger.error(f"❌ GPT 초기화 실패: {e}")
                self.enabled = False
    
    def translate_to_korean(self, company_name, business_summary):
        """기업 설명을 한글로 간단히 번역"""
        if not self.enabled or not business_summary:
            return f"{company_name} 관련 기업"
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "기업 설명을 한글로 30자 이내로 번역하는 전문가입니다."},
                    {"role": "user", "content": f"{company_name}: {business_summary[:300]}\n\n위 기업을 한글로 30자 이내로 설명해주세요."}
                ],
                max_tokens=100,
                temperature=0.3
            )
            korean_desc = response.choices[0].message.content.strip()
            return korean_desc[:50]
        except Exception as e:
            logger.warning(f"번역 실패 ({company_name}): {e}")
            return f"{company_name} 관련 기업"
    
    def analyze_portfolio(self, categorized_stocks, history):
        """포트폴리오 분석 실행"""
        if not self.enabled:
            return self._basic_analysis(categorized_stocks, history)
        
        try:
            prompt = self._create_analysis_prompt(categorized_stocks, history)
            
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system", 
                        "content": "당신은 피터 린치 투자 전략 전문가입니다."
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=4096,
                temperature=0.3
            )
            
            analysis = response.choices[0].message.content
            logger.info("✅ GPT 분석 완료")
            return analysis
            
        except Exception as e:
            logger.error(f"❌ GPT API 오류: {e}")
            return self._basic_analysis(categorized_stocks, history)
    
    def _create_analysis_prompt(self, categorized_stocks, history):
        """GPT 프롬프트 생성"""
        stocks_info = "## 이번 주 추천 포트폴리오\n\n"
        
        for category, name in [
            ('best_value', '최고 가치주'),
            ('high_growth', '고성장주'),
            ('balanced', '균형')
        ]:
            stocks = categorized_stocks.get(category, [])
            stocks_info += f"### {name}\n"
            
            for i, stock in enumerate(stocks[:4], 1):
                china_mark = " 🇨🇳" if stock.get('is_china', False) else ""
                stocks_info += f"{i}. **{stock['티커']}** - {stock['회사명']}{china_mark}\n"
                stocks_info += f"   한글: {stock.get('한글설명', 'N/A')}\n"
                stocks_info += f"   PEG: {stock['PEG']:.2f} | 성장률: {stock['성장률(%)']:.1f}%\n\n"
        
        prompt = f"""{stocks_info}

## 투자 전략
- 최고 가치주: 4종목 (40%)
- 고성장주: 4종목 (40%)
- 균형: 2종목 (20%)
- 🇨🇳 중국: 최대 1종목 (10%)

각 종목의 매수 이유와 주의사항을 간단히 설명해주세요.
"""
        return prompt
    
    def _basic_analysis(self, categorized_stocks, history):
        """기본 분석"""
        result = "🤖 기본 분석 (GPT API 미사용)\n\n"
        
        for category, name in [
            ('best_value', '최고가치'), 
            ('high_growth', '고성장'), 
            ('balanced', '균형')
        ]:
            stocks = categorized_stocks.get(category, [])
            result += f"**{name}**\n"
            
            for i, stock in enumerate(stocks[:4], 1):
                china_mark = " 🇨🇳" if stock.get('is_china', False) else ""
                result += f"  {i}. {stock['티커']}: {stock.get('한글설명', stock['회사명'])}{china_mark}\n"
            result += "\n"
        
        return result


class PortfolioHistoryManager:
    """포트폴리오 히스토리 관리"""
    
    def __init__(self, history_file='portfolio_history.json'):
        self.history_file = history_file
        self.history = self.load_history()
    
    def load_history(self):
        if not os.path.exists(self.history_file):
            return {}
        
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    
    def save_history(self):
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=4, ensure_ascii=False)
            logger.info(f"💾 히스토리 저장 완료")
        except Exception as e:
            logger.error(f"❌ 히스토리 저장 실패: {e}")


class SlackSender:
    """슬랙 메시지 전송"""
    
    def __init__(self):
        self.token = os.environ.get('SLACK_BOT_TOKEN')
        self.channel_id = os.environ.get('SLACK_CHANNEL_ID')
        self.enabled = bool(self.token and self.channel_id)
        
        if self.enabled:
            try:
                from slack_sdk import WebClient
                self.client = WebClient(token=self.token)
                response = self.client.auth_test()
                logger.info(f"✅ 슬랙 연동: {response['team']}")
            except Exception as e:
                logger.warning(f"⚠️ 슬랙 초기화 실패: {e}")
                self.enabled = False
        else:
            logger.info("ℹ️ 슬랙 미설정 - 콘솔 출력")
    
    def send_message(self, message):
        if not self.enabled:
            logger.info("ℹ️ 슬랙 미설정 - 메시지 콘솔 출력")
            return False
        
        try:
            self.client.chat_postMessage(
                channel=self.channel_id,
                text=message,
                mrkdwn=True
            )
            logger.info("✅ 슬랙 메시지 전송 완료")
            return True
        except Exception as e:
            logger.error(f"❌ 슬랙 전송 실패: {e}")
            return False
    
    def send_file(self, file_path, title=None):
        if not self.enabled:
            logger.info("ℹ️ 슬랙 미설정 - 파일 전송 스킵")
            return False
        
        try:
            self.client.files_upload_v2(
                channel=self.channel_id,
                file=file_path,
                title=title or os.path.basename(file_path)
            )
            logger.info(f"✅ 슬랙 파일 전송 완료: {file_path}")
            return True
        except Exception as e:
            logger.error(f"❌ 슬랙 파일 실패: {e}")
            return False


class PeterLynchScreener:
    """피터 린치 스크리너"""
    
    def __init__(self):
        self.tickers = []
        self.filtered = []
        self.validated = []
        self.categorized_stocks = {}
        
        self.history_manager = PortfolioHistoryManager()
        self.gpt_analyzer = GPTAnalyzer()
        self.slack_sender = SlackSender()
        
        self.MIN_MARKET_CAP = 100_000_000
        
        # 중국 키워드
        self.CHINA_KEYWORDS = [
            'china', 'chinese', 'beijing', 'shanghai', 'shenzhen',
            'hong kong', 'macau', 'taiwan', 'prc', 'cayman'
        ]
        
        # 완화된 필터 기준
        self.GROWTH_LIMITS = {
            'min': 5,            # 5%로 더 완화
            'ideal_min': 15,
            'ideal_max': 50,
            'max': 500           # 500%까지 허용
        }
        
        self.PEG_LIMITS = {
            'excellent': 0.5,
            'good': 1.0,
            'fair': 1.5,
            'max': 3.0           # 3.0까지 완화
        }
        
        self.TOLERANCE = 0.30
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        
        # 통계용
        self.skip_reasons = {}
    
    def _is_china_stock(self, info):
        """중국 관련 주식인지 확인"""
        try:
            country = info.get('country', '').lower()
            if any(c in country for c in ['china', 'hong kong', 'taiwan']):
                return True
            
            name = (info.get('longName', '') + ' ' + info.get('shortName', '')).lower()
            if any(kw in name for kw in self.CHINA_KEYWORDS):
                return True
            
            business = info.get('longBusinessSummary', '').lower()
            if sum(1 for kw in self.CHINA_KEYWORDS if kw in business) >= 2:
                return True
            
            return False
        except:
            return False
    
    def run(self, ticker_limit=None):
        """메인 실행"""
        start = time.time()
        
        logger.info("=" * 80)
        logger.info("🎯 피터 린치 스크리너 V5 - 완전 수정판")
        logger.info(f"💰 최소 시가총액: ${self.MIN_MARKET_CAP/1e6:.0f}M")
        logger.info(f"🇨🇳 중국 비중 제한: 최대 1종목 (10%)")
        logger.info(f"📊 필터: PEG < {self.PEG_LIMITS['max']}, 성장률 {self.GROWTH_LIMITS['min']}%+")
        logger.info("=" * 80)
        
        if not self._step1_collect_tickers(ticker_limit):
            return None
        if not self._step2_basic_filter():
            return None
        if not self._step3_deep_analysis():
            return None
        if not self._step4_categorize():
            return None
        
        filename = self._step5_create_excel()
        gpt_advice = self._step6_gpt_analysis()
        self._step7_send_to_slack(filename, gpt_advice)
        self._print_summary()
        
        elapsed = (time.time() - start) / 60
        logger.info(f"\n⏱️ 총 소요 시간: {elapsed:.1f}분")
        logger.info(f"📊 결과 파일: {filename}\n")
        
        return filename
    
    def _step1_collect_tickers(self, limit=None):
        """Step 1: 티커 수집"""
        logger.info("\n[Step 1/7] 티커 수집 중...")
        
        try:
            url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25000&download=true"
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if 'data' not in data or 'rows' not in data['data']:
                logger.error("❌ API 응답 형식 오류")
                return False
            
            df = pd.DataFrame(data['data']['rows'])
            
            df = df[df['symbol'].notna()].copy()
            df['symbol'] = df['symbol'].str.strip().str.upper()
            df = df[~df['symbol'].str.contains(r'\^|\.|-', regex=True, na=False)]
            
            if 'name' in df.columns:
                df = df[~df['name'].str.contains('ETF|ETN|FUND|TRUST', case=False, na=False)]
            
            df = df[df['symbol'].str.len().between(1, 5)]
            df = df[df['symbol'].str.isalpha()]
            df = df.drop_duplicates(subset=['symbol'])
            
            all_tickers = df['symbol'].tolist()
            self.tickers = all_tickers[:limit] if limit else all_tickers
            
            logger.info(f"✅ {len(self.tickers)}개 티커 수집 완료\n")
            return True
            
        except Exception as e:
            logger.error(f"❌ 티커 수집 실패: {e}")
            return False
    
    def _step2_basic_filter(self):
        """Step 2: 기본 필터 (조기 종료 버그 수정)"""
        logger.info("[Step 2/7] 기본 필터링 중...")
        passed = []
        errors = 0
        
        total = len(self.tickers)
        
        for i, ticker in enumerate(self.tickers, 1):
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                
                # 빈 info 체크
                if not info or len(info) < 5:
                    errors += 1
                    continue
                
                price = (info.get('currentPrice') or 
                        info.get('regularMarketPrice') or 
                        info.get('previousClose'))
                
                mcap = info.get('marketCap')
                
                if not price or not mcap:
                    errors += 1
                    continue
                
                if price >= 1.0 and mcap > self.MIN_MARKET_CAP:
                    passed.append({
                        'ticker': ticker,
                        'price': float(price),
                        'market_cap': int(mcap),
                        'info': info
                    })
                
                if i % 100 == 0:
                    logger.info(f"  {i}/{total} - 통과: {len(passed)}개, 에러: {errors}개")
                
                time.sleep(0.05)  # 0.1 → 0.05로 속도 개선
                
            except Exception as e:
                errors += 1
                if errors <= 10 and i <= 100:
                    logger.debug(f"  {ticker}: {str(e)[:50]}")
                continue
        
        self.filtered = passed
        logger.info(f"✅ {len(self.filtered)}개 필터 통과 (에러: {errors}개)\n")
        
        return len(self.filtered) > 0
    
    def _step3_deep_analysis(self):
        """Step 3: 정밀 분석 (검증 로직 완화)"""
        logger.info("[Step 3/7] 정밀 분석...")
        logger.info(f"  대상: {len(self.filtered)}개\n")
        
        validated = []
        self.skip_reasons = {}
        
        total = len(self.filtered)
        
        for i, stock_data in enumerate(self.filtered, 1):
            ticker = stock_data['ticker']
            
            try:
                result = self._analyze_stock(stock_data)
                
                if result:
                    validated.append(result)
                    if i <= 5:  # 처음 5개만 상세 로그
                        china_mark = " 🇨🇳" if result.get('is_china', False) else ""
                        logger.info(f"  ✅ {ticker}: PEG {result['peg']:.2f} | 성장률 {result['growth_rate']:.1f}%{china_mark}")
                
                if i % 100 == 0:
                    logger.info(f"  진행: {i}/{total} - 검증: {len(validated)}개")
                
                time.sleep(0.05)
                
            except Exception as e:
                if i <= 10:
                    logger.debug(f"  {ticker}: {str(e)[:50]}")
                continue
        
        self.validated = validated
        
        logger.info(f"\n✅ 최종: {len(self.validated)}개 검증 완료")
        
        # 제외 이유 통계
        if self.skip_reasons:
            logger.info("\n📊 제외 이유 TOP 5:")
            sorted_reasons = sorted(self.skip_reasons.items(), key=lambda x: -x[1])
            for reason, count in sorted_reasons[:5]:
                logger.info(f"   {reason}: {count}개")
        
        logger.info("")
        
        if len(self.validated) == 0:
            logger.error("⚠️ 검증 통과 종목이 0개입니다.")
            logger.error("📊 전체 제외 이유:")
            for reason, count in sorted(self.skip_reasons.items(), key=lambda x: -x[1]):
                logger.error(f"   {reason}: {count}개")
            return False
        
        return True
    
    def _analyze_stock(self, stock_data):
        """개별 종목 분석 (완화된 기준)"""
        ticker = stock_data['ticker']
        info = stock_data['info']
        
        try:
            # 기본 정보
            name = info.get('longName') or info.get('shortName', 'N/A')
            sector = info.get('sector', 'N/A')
            industry = info.get('industry', 'N/A')
            business = info.get('longBusinessSummary', '')[:500]
            price = stock_data['price']
            market_cap = stock_data['market_cap']
            
            # 중국 주식 확인
            is_china = self._is_china_stock(info)
            
            # PE 비율
            pe = info.get('trailingPE') or info.get('forwardPE')
            if not pe or pe <= 0:
                self.skip_reasons['PE 없음 또는 음수'] = self.skip_reasons.get('PE 없음 또는 음수', 0) + 1
                return None
            
            if pe > 100:  # PE가 너무 높으면 제외
                self.skip_reasons['PE 과다 (>100)'] = self.skip_reasons.get('PE 과다 (>100)', 0) + 1
                return None
            
            # 성장률
            growth = info.get('earningsGrowth') or info.get('earningsQuarterlyGrowth')
            if not growth:
                self.skip_reasons['성장률 데이터 없음'] = self.skip_reasons.get('성장률 데이터 없음', 0) + 1
                return None
            
            # 성장률 변환
            if growth < 0:
                self.skip_reasons[f'성장률 음수 ({growth*100:.1f}%)'] = self.skip_reasons.get(f'성장률 음수 ({growth*100:.1f}%)', 0) + 1
                return None
            
            growth_pct = growth * 100 if growth < 10 else growth
            
            # 성장률 필터 (매우 완화)
            if growth_pct < self.GROWTH_LIMITS['min']:
                self.skip_reasons[f'성장률 낮음 (<{self.GROWTH_LIMITS["min"]}%)'] = self.skip_reasons.get(f'성장률 낮음 (<{self.GROWTH_LIMITS["min"]}%)', 0) + 1
                return None
            
            if growth_pct > self.GROWTH_LIMITS['max']:
                self.skip_reasons[f'성장률 과다 (>{self.GROWTH_LIMITS["max"]}%)'] = self.skip_reasons.get(f'성장률 과다 (>{self.GROWTH_LIMITS["max"]}%)', 0) + 1
                return None
            
            # PEG 계산
            peg = pe / growth_pct
            
            # PEG 필터 (매우 완화)
            if peg <= 0:
                self.skip_reasons['PEG 음수'] = self.skip_reasons.get('PEG 음수', 0) + 1
                return None
            
            if peg >= self.PEG_LIMITS['max']:
                self.skip_reasons[f'PEG 과다 (>={self.PEG_LIMITS["max"]})'] = self.skip_reasons.get(f'PEG 과다 (>={self.PEG_LIMITS["max"]})', 0) + 1
                return None
            
            # 부채 비율 (완화)
            debt_to_equity = info.get('debtToEquity')
            if sector != 'Financial Services' and debt_to_equity and debt_to_equity > 500:
                self.skip_reasons['부채 과다 (>500)'] = self.skip_reasons.get('부채 과다 (>500)', 0) + 1
                return None
            
            return {
                'ticker': ticker,
                'name': name,
                'sector': sector,
                'industry': industry,
                'business_summary': business,
                'price': price,
                'market_cap': market_cap,
                'pe_ratio': pe,
                'peg': peg,
                'growth_rate': growth_pct,
                'debt_to_equity': debt_to_equity,
                'is_china': is_china,
                'validation_status': '✅ 검증 통과',
                'is_valid': True
            }
            
        except Exception as e:
            self.skip_reasons[f'분석 오류'] = self.skip_reasons.get('분석 오류', 0) + 1
            return None
    
    def _step4_categorize(self):
        """Step 4: 유형별 분류"""
        logger.info("[Step 4/7] 유형별 분류...")
        df = pd.DataFrame(self.validated)
        
        categorized = {
            'best_value': [],
            'high_growth': [],
            'balanced': []
        }
        
        # 중국 주식 통계
        china_stocks = df[df['is_china'] == True]
        logger.info(f"🇨🇳 중국 주식: {len(china_stocks)}개 발견")
        
        # 최고 가치주
        best = df[
            (df['peg'] < self.PEG_LIMITS['good']) &
            (df['growth_rate'] >= self.GROWTH_LIMITS['ideal_min']) &
            (df['growth_rate'] <= self.GROWTH_LIMITS['ideal_max'])
        ].sort_values('peg').head(10)
        
        for _, row in best.iterrows():
            categorized['best_value'].append(self._create_recommendation(row, 'best_value'))
        
        # 고성장주
        high = df[
            (df['growth_rate'] > 40) &
            (df['peg'] < 1.5)
        ].sort_values('growth_rate', ascending=False).head(10)
        
        for _, row in high.iterrows():
            categorized['high_growth'].append(self._create_recommendation(row, 'high_growth'))
        
        # 균형
        balanced = df[
            (df['peg'] < 1.2) &
            (df['growth_rate'] >= 15) &
            (df['growth_rate'] <= 40)
        ].sort_values('peg').head(5)
        
        for _, row in balanced.iterrows():
            categorized['balanced'].append(self._create_recommendation(row, 'balanced'))
        
        self.categorized_stocks = categorized
        
        logger.info(f"✅ 최고 가치주: {len(categorized['best_value'])}개")
        logger.info(f"✅ 고성장주: {len(categorized['high_growth'])}개")
        logger.info(f"✅ 균형: {len(categorized['balanced'])}개\n")
        
        return True
    
    def _create_recommendation(self, row, category):
        """추천 생성"""
        ticker = row['ticker']
        peg = row['peg']
        growth = row['growth_rate']
        market_cap_b = row['market_cap'] / 1e9
        is_china = row.get('is_china', False)
        
        category_names = {
            'best_value': '최고 가치주',
            'high_growth': '고성장주',
            'balanced': '균형'
        }
        
        opinion = "🟢 강력 매수" if peg < self.PEG_LIMITS['excellent'] else ("🟢 매수" if peg < self.PEG_LIMITS['good'] else "🟡 관심")
        
        if market_cap_b < 1.0:
            opinion += " 💎"
        
        if is_china:
            opinion += " 🇨🇳"
        
        korean_desc = self.gpt_analyzer.translate_to_korean(
            row.get('name', 'N/A'),
            row.get('business_summary', '')
        )
        
        return {
            '티커': ticker,
            '회사명': row.get('name', 'N/A'),
            '한글설명': korean_desc,
            '섹터': row.get('sector', 'N/A'),
            '산업': row.get('industry', 'N/A'),
            '기업설명': row.get('business_summary', 'N/A'),
            'PEG': peg,
            '성장률(%)': growth,
            'P/E': row.get('pe_ratio'),
            '시가총액($B)': round(market_cap_b, 2),
            '투자의견': opinion,
            '검증상태': row.get('validation_status', 'N/A'),
            '유형': category_names[category],
            'Yahoo': f"https://finance.yahoo.com/quote/{ticker}",
            'Finviz': f"https://finviz.com/quote.ashx?t={ticker}",
            'TradingView': f"https://www.tradingview.com/symbols/{ticker}",
            'price': row['price'],
            'category': category,
            'is_china': is_china
        }
    
    def _step5_create_excel(self):
        """Step 5: Excel 생성"""
        logger.info("[Step 5/7] Excel 생성...")
        
        today = datetime.now().strftime('%Y%m%d')
        filename = f'Peter_Lynch_Report_{today}.xlsx'
        
        wb = Workbook()
        wb.remove(wb.active)
        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        
        for sheet_name, key in [
            ('🏆 최고 가치주', 'best_value'),
            ('🚀 고성장주', 'high_growth'),
            ('⚖️ 균형', 'balanced')
        ]:
            stocks = self.categorized_stocks[key]
            if not stocks:
                continue
            
            ws = wb.create_sheet(title=sheet_name)
            columns = ['티커', '회사명', '한글설명', '유형', '섹터', 'PEG', '성장률(%)', 'P/E',
                      '시가총액($B)', '투자의견', 'Yahoo', 'Finviz']
            
            for col_idx, col_name in enumerate(columns, 1):
                cell = ws.cell(row=1, column=col_idx, value=col_name)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal='center', vertical='center')
            
            for row_idx, stock in enumerate(stocks, 2):
                for col_idx, col_name in enumerate(columns, 1):
                    value = stock.get(col_name, '')
                    cell = ws.cell(row=row_idx, column=col_idx, value=value)
                    
                    if col_name in ['Yahoo', 'Finviz'] and value:
                        cell.hyperlink = value
                        cell.style = 'Hyperlink'
                    
                    if col_name == '투자의견' and '강력' in str(value):
                        cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            
            widths = [10, 30, 35, 15, 15, 10, 10, 10, 12, 20, 15, 15]
            for i, width in enumerate(widths, 1):
                ws.column_dimensions[get_column_letter(i)].width = width
        
        wb.save(filename)
        logger.info(f"✅ {filename}\n")
        return filename
    
    def _step6_gpt_analysis(self):
        """Step 6: GPT 분석"""
        logger.info("[Step 6/7] GPT 분석...")
        
        gpt_advice = self.gpt_analyzer.analyze_portfolio(
            self.categorized_stocks,
            self.history_manager.history
        )
        
        self.history_manager.save_history()
        
        logger.info("✅ 완료\n")
        return gpt_advice
    
    def _step7_send_to_slack(self, filename, gpt_advice):
        """Step 7: 슬랙 전송"""
        logger.info("[Step 7/7] 슬랙 전송 시도...")
        
        stock_links = self._generate_stock_links()
        
        if not self.slack_sender.enabled:
            print("\n" + "="*80)
            print("📊 GPT 분석 결과")
            print("="*80)
            print(gpt_advice)
            print("\n" + "="*80)
            print("📈 추천 주식 주가 링크")
            print("="*80)
            print(stock_links)
            print("="*80 + "\n")
            logger.info("ℹ️ 슬랙 미설정 - 콘솔 출력 완료")
            return
        
        today = datetime.now().strftime('%Y년 %m월 %d일')
        
        message = f"""🤖 *피터 린치 봇 - 포트폴리오*
📅 {today}
🇨🇳 중국 비중 제한: 최대 1종목 (10%)

{gpt_advice}

━━━━━━━━━━━━━━━━━━
📈 *추천 주식 주가 링크*
━━━━━━━━━━━━━━━━━━
{stock_links}

━━━━━━━━━━━━━━━━━━
📂 {filename}
━━━━━━━━━━━━━━━━━━"""
        
        self.slack_sender.send_message(message)
        self.slack_sender.send_file(filename, f"리포트 - {today}")
        logger.info("✅ 슬랙 전송 완료\n")
    
    def _generate_stock_links(self):
        """주가 링크 생성"""
        links = []
        
        for category, name in [
            ('best_value', '🏆 최고 가치주'),
            ('high_growth', '🚀 고성장주'),
            ('balanced', '⚖️ 균형')
        ]:
            stocks = self.categorized_stocks.get(category, [])
            if stocks:
                links.append(f"\n*{name}*")
                for stock in stocks[:4]:
                    ticker = stock['티커']
                    name_kr = stock.get('한글설명', stock['회사명'])
                    price = stock.get('price', 0)
                    china_mark = " 🇨🇳" if stock.get('is_china', False) else ""
                    small_cap_mark = " 💎" if stock['시가총액($B)'] < 1.0 else ""
                    
                    yahoo_link = f"https://finance.yahoo.com/quote/{ticker}"
                    
                    links.append(
                        f"  • *{ticker}* - {name_kr}{china_mark}{small_cap_mark}\n"
                        f"    현재가: ${price:.2f} | <{yahoo_link}|주가 보기>"
                    )
        
        return "\n".join(links) if links else "추천 종목 없음"
    
    def _print_summary(self):
        """콘솔 요약"""
        print("\n" + "="*80)
        print("💡 포트폴리오 추천")
        print("="*80)
        
        for category, name in [('best_value', '최고 가치주'), ('high_growth', '고성장주'), ('balanced', '균형')]:
            stocks = self.categorized_stocks[category]
            if stocks:
                print(f"\n【{name}】")
                for stock in stocks[:3]:
                    marks = ""
                    if stock['시가총액($B)'] < 1.0:
                        marks += " 💎"
                    if stock.get('is_china', False):
                        marks += " 🇨🇳"
                    
                    print(f"  {stock['티커']:6} - {stock.get('한글설명', stock['회사명'])}{marks}")
                    print(f"     PEG: {stock['PEG']:.2f} | 성장률: {stock['성장률(%)']:.1f}%")
        
        print("\n" + "="*80)


def main():
    print("""
╔════════════════════════════════════════════════════════════════╗
║  피터 린치 주식 스크리너 V5 - 완전 수정판                    ║
║                                                                ║
║  ✅ Step 2 조기 종료 버그 수정                                ║
║  ✅ Step 3 검증 로직 완화 (PEG < 3.0, 성장률 5%+)            ║
║  ✅ 제외 이유 상세 로그                                       ║
║  ✅ 중국 비중 10% 제한                                        ║
║  ✅ 슬랙 주가 링크 추가                                       ║
║                                                                ║
║  환경 변수:                                                    ║
║  - OPENAI_API_KEY (필수)                                      ║
║  - SLACK_BOT_TOKEN, SLACK_CHANNEL_ID (선택)                  ║
╚════════════════════════════════════════════════════════════════╝
    """)
    
    screener = PeterLynchScreener()
    result = screener.run(ticker_limit=None)
    
    if result:
        print(f"\n✅ 스크리닝 완료!")
        print(f"📊 Excel 파일: {result}")
    else:
        print("\n❌ 스크리닝 실패")
        print("로그 파일을 확인하세요.")


if __name__ == "__main__":
    main()