"""
피터 린치식 미국 주식 스크리닝 봇 V5 - GitHub Actions 자동화
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'screener_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class GPTAnalyzer:
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.portfolio_allocation = {
            'best_value': {'weight': 0.40, 'stocks': 4},
            'high_growth': {'weight': 0.40, 'stocks': 4},
            'balanced': {'weight': 0.20, 'stocks': 2}
        }
        self.position_size = 10
        
        if not self.api_key:
            logger.warning("⚠️ OPENAI_API_KEY 미설정")
            self.enabled = False
        else:
            try:
                self.client = OpenAI(api_key=self.api_key)
                self.enabled = True
                logger.info("✅ GPT API 연동")
            except Exception as e:
                logger.error(f"❌ GPT 초기화 실패: {e}")
                self.enabled = False
    
    def analyze_portfolio(self, categorized_stocks, history):
        if not self.enabled:
            return self._basic_analysis(categorized_stocks, history)
        
        try:
            prompt = self._create_prompt(categorized_stocks, history)
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "당신은 피터 린치 투자 전략 전문가입니다."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=4096,
                temperature=0.3
            )
            logger.info("✅ GPT 분석 완료")
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ GPT 오류: {e}")
            return self._basic_analysis(categorized_stocks, history)
    
    def _create_prompt(self, stocks, history):
        prompt = "## 이번 주 추천 포트폴리오\n\n"
        for cat, info in [('best_value', '최고가치'), ('high_growth', '고성장'), ('balanced', '균형')]:
            st = stocks.get(cat, [])
            prompt += f"### {info}\n"
            for s in st[:self.portfolio_allocation[cat]['stocks']]:
                prompt += f"- {s['티커']}: PEG {s['PEG']:.2f}, 성장률 {s['성장률(%)']:.1f}%\n"
            prompt += "\n"
        return prompt
    
    def _basic_analysis(self, stocks, history):
        result = "🤖 기본 분석\n\n"
        for cat, name in [('best_value', '최고가치'), ('high_growth', '고성장'), ('balanced', '균형')]:
            result += f"**{name}**\n"
            for s in stocks.get(cat, [])[:self.portfolio_allocation[cat]['stocks']]:
                result += f"  {s['티커']}: PEG {s['PEG']:.2f}\n"
        return result


class PortfolioHistoryManager:
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
        except Exception as e:
            logger.error(f"❌ 저장 실패: {e}")
    
    def update_from_portfolio(self, categorized_stocks):
        today = datetime.now().strftime("%Y-%m-%d")
        current = set()
        for cat, stocks in categorized_stocks.items():
            for s in stocks:
                ticker = s['티커'].upper()
                current.add(ticker)
                if ticker in self.history:
                    self.history[ticker]['last_update'] = today
                    self.history[ticker]['current_price'] = s['price']
        
        for ticker in list(self.history.keys()):
            if ticker not in current and self.history[ticker].get('status') != 'REMOVED':
                self.history[ticker]['status'] = 'REMOVED'
                self.history[ticker]['removed_date'] = today
        
        self.save_history()


class SlackSender:
    def __init__(self):
        self.token = os.environ.get('SLACK_BOT_TOKEN')
        self.channel_id = os.environ.get('SLACK_CHANNEL_ID')
        self.enabled = bool(self.token and self.channel_id)
        
        if self.enabled:
            try:
                from slack_sdk import WebClient
                self.client = WebClient(token=self.token)
                logger.info("✅ 슬랙 연동")
            except:
                self.enabled = False
    
    def send_message(self, message):
        if not self.enabled:
            return False
        try:
            self.client.chat_postMessage(channel=self.channel_id, text=message, mrkdwn=True)
            logger.info("✅ 슬랙 메시지 전송")
            return True
        except Exception as e:
            logger.error(f"❌ 슬랙 실패: {e}")
            return False
    
    def send_file(self, file_path, title=None):
        if not self.enabled:
            return False
        try:
            self.client.files_upload_v2(channel=self.channel_id, file=file_path, title=title)
            logger.info("✅ 슬랙 파일 전송")
            return True
        except:
            return False


class PeterLynchScreener:
    def __init__(self):
        self.tickers = []
        self.filtered = []
        self.validated = []
        self.categorized_stocks = {}
        
        self.history_manager = PortfolioHistoryManager()
        self.gpt_analyzer = GPTAnalyzer()
        self.slack_sender = SlackSender()
        
        self.GROWTH_LIMITS = {'min': 15, 'ideal_min': 20, 'ideal_max': 50, 'max': 200}
        self.PEG_LIMITS = {'excellent': 0.5, 'good': 0.7, 'fair': 1.0, 'max': 1.5}
        self.TOLERANCE = 0.20
        
        self.headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
    
    def run(self, ticker_limit=1000):
        start = time.time()
        logger.info("="*80)
        logger.info("🎯 피터 린치 스크리너 V5")
        logger.info("="*80)
        
        if not self._collect_tickers(ticker_limit): return None
        if not self._basic_filter(): return None
        if not self._deep_analysis(): return None
        if not self._categorize(): return None
        
        filename = self._create_excel()
        gpt_advice = self._gpt_analysis()
        self._send_slack(filename, gpt_advice)
        
        logger.info(f"\n⏱️ 소요: {(time.time()-start)/60:.1f}분")
        return filename
    
    def _collect_tickers(self, limit):
        logger.info("\n[1/7] 티커 수집...")
        try:
            url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25000&download=true"
            data = requests.get(url, headers=self.headers, timeout=30).json()
            df = pd.DataFrame(data['data']['rows'])
            df = df[df['symbol'].notna()].copy()
            df['symbol'] = df['symbol'].str.strip().str.upper()
            df = df[~df['symbol'].str.contains(r'\^|\.|-', regex=True, na=False)]
            if 'name' in df.columns:
                df = df[~df['name'].str.contains('ETF|ETN|FUND|TRUST', case=False, na=False)]
            df = df[df['symbol'].str.len().between(1, 5)]
            df = df[df['symbol'].str.isalpha()]
            df = df.drop_duplicates(subset=['symbol'])
            self.tickers = df['symbol'].tolist()[:limit]
            logger.info(f"✅ {len(self.tickers)}개\n")
            return True
        except Exception as e:
            logger.error(f"❌ 실패: {e}")
            return False
    
    def _basic_filter(self):
        logger.info("[2/7] 기본 필터...")
        passed = []
        for i, ticker in enumerate(self.tickers, 1):
            try:
                stock = yf.Ticker(ticker)
                fast = stock.fast_info
                price = fast.get('last_price')
                mcap = fast.get('market_cap')
                if price and mcap and price >= 1.0 and mcap > 1_000_000_000:
                    passed.append({'ticker': ticker, 'price': price, 'market_cap': mcap})
                if i % 100 == 0:
                    logger.info(f"  {i}/{len(self.tickers)} - {len(passed)}개")
                time.sleep(0.05)
            except:
                continue
        self.filtered = passed
        logger.info(f"✅ {len(self.filtered)}개\n")
        return len(self.filtered) > 0
    
    def _deep_analysis(self):
        logger.info("[3/7] 심층 분석...")
        validated = []
        for i, sd in enumerate(self.filtered, 1):
            try:
                result = self._analyze_stock(sd)
                if result and result['is_valid']:
                    validated.append(result)
                if i % 25 == 0:
                    logger.info(f"  {i}/{len(self.filtered)} - {len(validated)}개")
                time.sleep(0.3)
            except:
                continue
        self.validated = validated
        logger.info(f"✅ {len(self.validated)}개\n")
        return len(self.validated) > 0
    
    def _analyze_stock(self, basic_data):
        ticker = basic_data['ticker']
        stock = yf.Ticker(ticker)
        info = stock.info
        
        yahoo_pe = info.get('trailingPE') or info.get('forwardPE')
        yahoo_growth = info.get('earningsGrowth')
        if not yahoo_pe or not yahoo_growth: return None
        
        growth_pct = yahoo_growth * 100
        if growth_pct <= 0: return None
        
        peg = yahoo_pe / growth_pct
        if peg >= self.PEG_LIMITS['max'] or growth_pct < self.GROWTH_LIMITS['min']:
            return None
        
        return {
            'ticker': ticker,
            'name': info.get('longName', 'N/A'),
            'sector': info.get('sector', 'N/A'),
            'industry': info.get('industry', 'N/A'),
            'business_summary': info.get('longBusinessSummary', '')[:500],
            'price': basic_data['price'],
            'market_cap': basic_data['market_cap'],
            'pe_ratio': yahoo_pe,
            'peg': peg,
            'growth_rate': growth_pct,
            'validation_status': '✅ 검증',
            'data_sources': ['Yahoo'],
            'is_valid': True
        }
    
    def _categorize(self):
        logger.info("[4/7] 유형 분류...")
        df = pd.DataFrame(self.validated)
        
        categorized = {'best_value': [], 'high_growth': [], 'balanced': []}
        
        best = df[(df['peg'] < 0.7) & (df['growth_rate'] >= 20) & (df['growth_rate'] <= 50) & 
                  (df['market_cap'] > 5e9)].sort_values('peg').head(10)
        for _, row in best.iterrows():
            categorized['best_value'].append(self._create_rec(row, 'best_value'))
        
        high = df[(df['growth_rate'] > 50) & (df['growth_rate'] <= 200) & (df['peg'] < 1.2) & 
                  (df['market_cap'] > 3e9)].sort_values('growth_rate', ascending=False).head(10)
        for _, row in high.iterrows():
            categorized['high_growth'].append(self._create_rec(row, 'high_growth'))
        
        bal = df[(df['peg'] < 1.0) & (df['growth_rate'] >= 20) & (df['growth_rate'] <= 40) & 
                 (df['market_cap'] > 10e9)].sort_values('peg').head(5)
        for _, row in bal.iterrows():
            categorized['balanced'].append(self._create_rec(row, 'balanced'))
        
        self.categorized_stocks = categorized
        logger.info(f"✅ 최고가치: {len(categorized['best_value'])}개")
        logger.info(f"✅ 고성장: {len(categorized['high_growth'])}개")
        logger.info(f"✅ 균형: {len(categorized['balanced'])}개\n")
        return True
    
    def _create_rec(self, row, cat):
        return {
            '티커': row['ticker'],
            '회사명': row.get('name', 'N/A'),
            '섹터': row.get('sector', 'N/A'),
            '산업': row.get('industry', 'N/A'),
            '기업설명': row.get('business_summary', 'N/A'),
            'PEG': row['peg'],
            '성장률(%)': row['growth_rate'],
            'P/E': row.get('pe_ratio'),
            '시가총액($B)': round(row['market_cap'] / 1e9, 1),
            '투자의견': "🟢 강력매수" if row['peg'] < 0.5 else "🟢 매수",
            '검증상태': row['validation_status'],
            'price': row['price'],
            'category': cat
        }
    
    def _create_excel(self):
        logger.info("[5/7] Excel 생성...")
        today = datetime.now().strftime('%Y%m%d')
        filename = f'Peter_Lynch_Report_{today}.xlsx'
        
        wb = Workbook()
        wb.remove(wb.active)
        
        for name, key in [('최고가치', 'best_value'), ('고성장', 'high_growth'), ('균형', 'balanced')]:
            stocks = self.categorized_stocks[key]
            if not stocks: continue
            
            ws = wb.create_sheet(title=name)
            cols = ['티커', '회사명', '섹터', 'PEG', '성장률(%)', 'P/E', '시가총액($B)', '투자의견']
            
            for i, col in enumerate(cols, 1):
                cell = ws.cell(row=1, column=i, value=col)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
            
            for i, stock in enumerate(stocks, 2):
                for j, col in enumerate(cols, 1):
                    ws.cell(row=i, column=j, value=stock.get(col, ''))
        
        wb.save(filename)
        logger.info(f"✅ {filename}\n")
        return filename
    
    def _gpt_analysis(self):
        logger.info("[6/7] GPT 분석...")
        advice = self.gpt_analyzer.analyze_portfolio(self.categorized_stocks, self.history_manager.history)
        self.history_manager.update_from_portfolio(self.categorized_stocks)
        logger.info("✅ 완료\n")
        return advice
    
    def _send_slack(self, filename, advice):
        logger.info("[7/7] 전송...")
        if not self.slack_sender.enabled:
            print("\n" + "="*80)
            print("📊 GPT 분석 결과")
            print("="*80)
            print(advice)
            print("="*80 + "\n")
            return
        
        today = datetime.now().strftime('%Y년 %m월 %d일')
        msg = f"🤖 *피터 린치 봇*\n📅 {today}\n\n{advice}"
        self.slack_sender.send_message(msg)
        self.slack_sender.send_file(filename, f"리포트 - {today}")
        logger.info("✅ 완료\n")


def main():
    screener = PeterLynchScreener()
    result = screener.run(ticker_limit=1000)
    if result:
        print(f"\n✅ 완료: {result}")
    else:
        print("\n❌ 실패")


if __name__ == "__main__":
    main()
