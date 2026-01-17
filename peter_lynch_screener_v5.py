"""
피터 린치식 미국 주식 스크리닝 봇 V5.1 - Final (Safe Mode)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
기능:
1. 속도 최적화 (fast_info 사용)
2. 중국 주식 비중 10% 제한 (포트폴리오 당 1개)
3. 소형주($100M+) Tenbagger 발굴
4. 슬랙 메시지에 주가 확인 링크(Yahoo) 제공
5. 히스토리 보존 모드 (기본값)

실행: python peter_lynch_bot_v5.py
"""

import pandas as pd
import yfinance as yf
import requests
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

# 경고 무시 및 로깅 설정
warnings.filterwarnings('ignore')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[
        logging.FileHandler(f'screener_log.txt', mode='w'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class GPTAnalyzer:
    """GPT API: 포트폴리오 분석 및 한글 번역"""
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            logger.warning("⚠️ OPENAI_API_KEY 미설정. 기본 문구만 출력합니다.")
            self.enabled = False
        else:
            self.client = OpenAI(api_key=self.api_key)
            self.enabled = True

    def translate_to_korean(self, company_name, business_summary):
        if not self.enabled or not business_summary:
            return f"{company_name} 관련 기업"
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "기업 설명을 한글로 50자 이내로 요약 번역."},
                    {"role": "user", "content": f"{company_name}: {business_summary[:300]}"}
                ],
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
        except:
            return f"{company_name} 관련 기업"

    def analyze_portfolio(self, categorized_stocks, history):
        if not self.enabled: return "GPT API 키가 없습니다."
        
        prompt_text = "## 이번 주 추천 포트폴리오 (중국 비중 10% 제한 적용됨)\n"
        for cat, stocks in categorized_stocks.items():
            prompt_text += f"\n[{cat.upper()}]\n"
            for s in stocks:
                prompt_text += f"- {s['티커']} ({s['회사명']}): PEG {s['PEG']}, 성장률 {s['성장률(%)']}%\n"
        
        prompt_text += "\n위 종목들에 대해 '1주차 3% 분할 매수' 관점에서 액션 플랜을 짧고 굵게 작성해줘. 소형주의 잠재력도 언급해줘."

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "당신은 피터 린치 투자 전문가입니다."},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.5
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"GPT 분석 실패: {e}"

class PortfolioHistoryManager:
    """포트폴리오 히스토리 (기억) 관리"""
    def __init__(self, history_file='portfolio_history.json', reset=False):
        self.history_file = history_file
        
        # 여기서 reset=True일 때만 파일을 지웁니다.
        if reset and os.path.exists(self.history_file):
            os.remove(self.history_file)
            logger.info("🧹 기존 히스토리를 삭제하고 새로 시작합니다.")
        
        self.history = self.load_history()
        self.MAX_STAGE = 3
        self.STAGE_WEIGHTS = {1: 3, 2: 3, 3: 4}

    def load_history(self):
        if not os.path.exists(self.history_file): return {}
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return {}

    def save_history(self):
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, indent=4, ensure_ascii=False)

    def update_from_portfolio(self, categorized_stocks):
        today = datetime.now().strftime("%Y-%m-%d")
        all_recommended = [s['티커'] for cat in categorized_stocks.values() for s in cat]
        
        # 기존 보유 종목 업데이트
        for ticker, info in self.history.items():
            if info['status'] == 'ACTIVE':
                if ticker in all_recommended:
                    if info['stage'] < self.MAX_STAGE:
                        info['stage'] += 1
                        info['current_weight_pct'] += self.STAGE_WEIGHTS[info['stage']]
                    info['last_update'] = today
                else:
                    logger.info(f"⚠️ {ticker}: 추천 제외됨 (관망 필요)")
        
        # 신규 종목 추가 로직은 간소화를 위해 생략되었으나, 
        # 실제로는 여기서 history 딕셔너리에 새로운 종목을 추가해야 다음 주에 '기존 종목'으로 인식합니다.
        for ticker in all_recommended:
            if ticker not in self.history:
                self.history[ticker] = {
                    'ticker': ticker,
                    'status': 'ACTIVE',
                    'stage': 1,
                    'current_weight_pct': self.STAGE_WEIGHTS[1],
                    'entry_date': today,
                    'last_update': today
                }
        
        self.save_history()

class SlackSender:
    """슬랙 전송 관리"""
    def __init__(self):
        self.token = os.environ.get('SLACK_BOT_TOKEN')
        self.channel_id = os.environ.get('SLACK_CHANNEL_ID')
        self.enabled = bool(self.token and self.channel_id)
        if self.enabled:
            try:
                from slack_sdk import WebClient
                self.client = WebClient(token=self.token)
            except:
                self.enabled = False

    def send_report(self, message, file_path):
        if not self.enabled: 
            print("\n[슬랙 미설정] 결과가 콘솔에 출력됩니다.")
            print(message)
            return
        try:
            self.client.chat_postMessage(channel=self.channel_id, text=message, mrkdwn=True)
            self.client.files_upload_v2(channel=self.channel_id, file=file_path, title="투자 리포트")
            logger.info("✅ 슬랙 전송 완료")
        except Exception as e:
            logger.error(f"슬랙 전송 실패: {e}")

class PeterLynchScreener:
    """메인 스크리너"""
    def __init__(self, reset_history=False):
        self.tickers = []
        self.filtered = []
        self.validated = []
        self.categorized_stocks = {}
        
        self.history_manager = PortfolioHistoryManager(reset=reset_history)
        self.gpt_analyzer = GPTAnalyzer()
        self.slack_sender = SlackSender()
        
        self.MIN_MARKET_CAP = 100_000_000 # $100M
        self.PEG_LIMITS = {'max': 2.0}
        self.headers = {'User-Agent': 'Mozilla/5.0'}
        self.china_stock_count = 0

    def run(self):
        logger.info(f"🚀 스크리너 시작 (최소 시총: ${self.MIN_MARKET_CAP/1e6:,.0f}M)")
        
        if not self._step1_collect_tickers(): return
        if not self._step2_fast_filter(): return
        if not self._step3_deep_analysis(): return
        if not self._step4_categorize(): return
        
        filename = self._step5_create_excel()
        gpt_advice = self._step6_gpt_analysis()
        self._step7_send_result(filename, gpt_advice)

    def _step1_collect_tickers(self):
        logger.info("[1/7] 티커 수집 (NASDAQ API)...")
        try:
            url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25000&download=true"
            res = requests.get(url, headers=self.headers, timeout=30)
            df = pd.DataFrame(res.json()['data']['rows'])
            df = df[df['symbol'].str.isalpha()]
            self.tickers = df['symbol'].tolist()
            logger.info(f"✅ {len(self.tickers)}개 티커 확보")
            return True
        except Exception as e:
            logger.error(f"티커 수집 실패: {e}")
            return False

    def _step2_fast_filter(self):
        logger.info("[2/7] 고속 필터링 (Fast Info)...")
        passed = []
        for i, ticker in enumerate(self.tickers):
            try:
                stock = yf.Ticker(ticker)
                price = stock.fast_info.last_price
                mcap = stock.fast_info.market_cap
                
                if price and mcap and price >= 1.0 and mcap > self.MIN_MARKET_CAP:
                    passed.append({'ticker': ticker, 'price': price, 'market_cap': mcap})
                
                if i % 1000 == 0: logger.info(f"  진행중... {i}/{len(self.tickers)}")
            except: continue
            
        self.filtered = passed
        logger.info(f"✅ 1차 통과: {len(self.filtered)}개")
        return len(self.filtered) > 0

    def _step3_deep_analysis(self):
        logger.info("[3/7] 정밀 분석 (3중 검증 로직)...")
        validated = []
        for i, data in enumerate(self.filtered):
            res = self._analyze_stock(data)
            if res: validated.append(res)
            if i % 100 == 0: logger.info(f"  분석중... {i}/{len(self.filtered)}")
            
        self.validated = validated
        logger.info(f"✅ 최종 검증 완료: {len(self.validated)}개")
        return len(self.validated) > 0

    def _analyze_stock(self, data):
        try:
            stock = yf.Ticker(data['ticker'])
            info = stock.info
            
            pe = info.get('trailingPE') or info.get('forwardPE')
            growth = info.get('earningsGrowth')
            
            if not pe or not growth: return None
            
            growth_pct = growth * 100
            if growth_pct <= 5: return None
            
            peg = pe / growth_pct
            
            if peg > self.PEG_LIMITS['max'] or peg <= 0: return None
            
            debt = info.get('debtToEquity')
            sector = info.get('sector', '')
            if sector != 'Financial Services' and debt and debt > 200: return None

            return {
                'ticker': data['ticker'],
                'name': info.get('longName', data['ticker']),
                'sector': sector,
                'industry': info.get('industry', ''),
                'business_summary': info.get('longBusinessSummary', ''),
                'price': data['price'],
                'market_cap': data['market_cap'],
                'pe_ratio': pe,
                'peg': peg,
                'growth_rate': growth_pct
            }
        except: return None

    def _is_china_stock(self, stock):
        keywords = ['China', 'Chinese', 'Hong Kong', 'Macau', 'Beijing']
        text = (stock['name'] + " " + stock['business_summary']).lower()
        return any(k.lower() in text for k in keywords)

    def _step4_categorize(self):
        logger.info("[4/7] 포트폴리오 분류 (중국 비중 제한)...")
        df = pd.DataFrame(self.validated)
        categorized = {'best_value': [], 'high_growth': [], 'balanced': []}
        
        df = df.sort_values('peg')
        
        self.china_stock_count = 0
        MAX_CHINA = 1 
        
        for _, row in df.iterrows():
            cat = ''
            if row['peg'] < 0.7 and 20 <= row['growth_rate'] <= 50: cat = 'best_value'
            elif row['growth_rate'] > 50 and row['peg'] < 1.2: cat = 'high_growth'
            elif row['peg'] < 1.0 and 15 <= row['growth_rate'] <= 40: cat = 'balanced'
            else: continue
            
            limit = 2 if cat == 'balanced' else 4
            if len(categorized[cat]) >= limit: continue
            
            if self._is_china_stock(row):
                if self.china_stock_count >= MAX_CHINA: continue
                self.china_stock_count += 1
                row['name'] += " (🇨🇳China)"
            
            korean_desc = self.gpt_analyzer.translate_to_korean(row['name'], row['business_summary'])
            stock_data = row.to_dict()
            stock_data['한글설명'] = korean_desc
            stock_data['Yahoo'] = f"https://finance.yahoo.com/quote/{row['ticker']}"
            
            categorized[cat].append(stock_data)
            
            if sum(len(v) for v in categorized.values()) >= 10: break
            
        self.categorized_stocks = categorized
        logger.info(f"✅ 분류 완료 (중국 주식: {self.china_stock_count}개 포함)")
        return True

    def _step5_create_excel(self):
        logger.info("[5/7] 엑셀 파일 생성...")
        filename = f'Peter_Lynch_Report_{datetime.now().strftime("%Y%m%d")}.xlsx'
        wb = Workbook()
        wb.remove(wb.active)
        
        for cat, title in [('best_value', '🏆최고가치'), ('high_growth', '🚀고성장'), ('balanced', '⚖️균형')]:
            ws = wb.create_sheet(title)
            ws.append(['티커', '회사명', '한글설명', 'PEG', '성장률', '주가', 'Yahoo_Link'])
            for s in self.categorized_stocks[cat]:
                ws.append([s['ticker'], s['name'], s['한글설명'], round(s['peg'],2), round(s['growth_rate'],1), round(s['price'],2), s['Yahoo']])
        
        wb.save(filename)
        return filename

    def _step6_gpt_analysis(self):
        logger.info("[6/7] GPT 투자 조언 생성...")
        advice = self.gpt_analyzer.analyze_portfolio(self.categorized_stocks, self.history_manager.history)
        self.history_manager.update_from_portfolio(self.categorized_stocks)
        return advice

    def _step7_send_result(self, filename, advice):
        logger.info("[7/7] 결과 전송...")
        
        links_text = "\n🔗 *실시간 주가 확인*\n"
        for cat, stocks in self.categorized_stocks.items():
            for s in stocks:
                links_text += f"• <{s['Yahoo']}|{s['ticker']}> : {s['name']}\n"
        
        message = f"""🤖 *피터 린치 봇 리포트* ({datetime.now().strftime('%Y-%m-%d')})
        
{advice}

{links_text}
"""
        self.slack_sender.send_report(message, filename)

if __name__ == "__main__":
    # reset_history=False (기본값): 히스토리를 유지함 (계속 기억함)
    # 이번 한번만 초기화하고 싶으면:
    # 1. 파일 탐색기에서 'portfolio_history.json' 파일을 삭제하세요.
    # 2. 그리고 아래 코드를 그대로 실행하세요. 
    bot = PeterLynchScreener(reset_history=False)
    bot.run()