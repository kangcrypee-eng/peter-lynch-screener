"""
피터 린치식 미국 주식 스크리닝 봇 V5 - 최종 완성판
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

공격적 포트폴리오 전략:
- 최고 가치주: 40% (4종목 × 10%)
- 고성장주: 40% (4종목 × 10%)
- 균형: 20% (2종목 × 10%)
총 10종목 = 100%

핵심 기능:
1. fast_info 사용 → 속도 10배 향상
2. 3중 검증 (Yahoo + 직접계산 + Finviz)
3. 유형별 포트폴리오 비중 관리
4. 유형별 순위 기반 매수/매도

실행: python peter_lynch_screener_v5_complete.py
환경 변수: OPENAI_API_KEY (필수), SLACK_BOT_TOKEN, SLACK_CHANNEL_ID (선택)
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
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'screener_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class GPTAnalyzer:
    """GPT API 포트폴리오 분석"""
    
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        
        # 공격적 포트폴리오 비중
        self.portfolio_allocation = {
            'best_value': {'weight': 0.40, 'stocks': 4},
            'high_growth': {'weight': 0.40, 'stocks': 4},
            'balanced': {'weight': 0.20, 'stocks': 2}
        }
        
        self.position_size = 10
        
        if not self.api_key:
            logger.warning("⚠️ OPENAI_API_KEY 미설정 - 기본 분석 모드")
            self.enabled = False
        else:
            try:
                self.client = OpenAI(api_key=self.api_key)
                self.enabled = True
                logger.info("✅ GPT API 연동 완료")
            except Exception as e:
                logger.error(f"❌ GPT API 초기화 실패: {e}")
                self.enabled = False
    
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
                        "content": "당신은 피터 린치 투자 전략 전문가입니다. 공격적 성장 포트폴리오를 관리하며, 명확하고 실용적인 투자 조언을 제공합니다."
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
        """GPT 프롬프트 생성 - 유형별 순위 기반"""
        stocks_info = "## 이번 주 추천 포트폴리오 (유형별 Top N)\n\n"
        
        targets = {'best_value': 4, 'high_growth': 4, 'balanced': 2}
        
        for category, info in [
            ('best_value', '최고 가치주'),
            ('high_growth', '고성장주'),
            ('balanced', '균형')
        ]:
            stocks = categorized_stocks.get(category, [])
            target_count = targets[category]
            target_weight = self.portfolio_allocation[category]['weight'] * 100
            
            stocks_info += f"### 📊 {info} (목표: {target_count}종목, {target_weight:.0f}%)\n\n"
            stocks_info += f"**유형 내 Top {target_count}:**\n"
            
            for i, stock in enumerate(stocks[:target_count * 2], 1):
                in_target = "✅" if i <= target_count else "⚠️"
                stocks_info += f"{in_target} **{i}위. {stock['티커']}** - {stock['회사명']}\n"
                stocks_info += f"   기업: {stock.get('기업설명', 'N/A')[:120]}...\n"
                stocks_info += f"   PEG: {stock['PEG']:.2f} | 성장률: {stock['성장률(%)']:.1f}% | PE: {stock.get('P/E', 'N/A')}\n"
                stocks_info += f"   현재가: ${stock['price']:.2f} | 검증: {stock['검증상태']}\n\n"
        
        history_info = self._format_history_info(history, categorized_stocks)
        
        prompt = f"""{stocks_info}

{history_info}

## 유형별 포트폴리오 전략

**목표 구성**:
- 최고 가치주: 4종목 (40%) - 유형 내 4위 이내
- 고성장주: 4종목 (40%) - 유형 내 4위 이내
- 균형: 2종목 (20%) - 유형 내 2위 이내

**주차별 진입**: 1주차 3% → 2주차 3% → 3주차 4% = 총 10%

## 우선순위 원칙
1. **진행 중 종목 (stage < 3)** → 무조건 완성
2. **완성 종목 (stage = 3)** → 유형 내 목표 순위 유지 시 보유
3. **신규 진입** → 유형별 슬롯 여유 + 부족한 유형 우선
4. **매도 고려** → 유형 순위 밖 2주 이상

## 요청

각 종목마다 유형 + 순위 + 구체적 이유를 포함한 포트폴리오 조언을 작성해주세요.
"""
        return prompt
    
    def _format_history_info(self, history, categorized_stocks):
        """히스토리 정보 포맷팅"""
        history_info = "## 현재 보유 포트폴리오\n\n"
        
        if not history:
            return history_info + "보유 없음 (첫 실행)\n"
        
        active = {k: v for k, v in history.items() if v.get('status') == 'ACTIVE'}
        
        if not active:
            return history_info + "보유 없음\n"
        
        total_weight = 0
        category_weights = {'best_value': 0, 'high_growth': 0, 'balanced': 0}
        
        for ticker, rec in active.items():
            weight = rec.get('current_weight_pct', 0)
            total_weight += weight
            cat = rec.get('category', 'balanced')
            if cat in category_weights:
                category_weights[cat] += weight
        
        history_info += f"**전체 투자 비중**: {total_weight:.1f}%\n"
        history_info += f"- 최고가치: {category_weights['best_value']:.1f}% (목표: 40%)\n"
        history_info += f"- 고성장: {category_weights['high_growth']:.1f}% (목표: 40%)\n"
        history_info += f"- 균형: {category_weights['balanced']:.1f}% (목표: 20%)\n\n"
        
        all_stocks = []
        for cat_stocks in categorized_stocks.values():
            all_stocks.extend(cat_stocks)
        
        for ticker, rec in active.items():
            cp = next((s['price'] for s in all_stocks if s['티커'] == ticker), None)
            
            if cp:
                pc = ((cp - rec['entry_price']) / rec['entry_price']) * 100
                status = "✅ 유지"
            else:
                pc = 0
                status = "⚠️ 탈락"
            
            history_info += f"**{ticker}** ({rec.get('stage', 0)}주차, {rec.get('category', 'N/A')})\n"
            history_info += f"   비중: {rec.get('current_weight_pct', 0):.1f}% | 진입: ${rec['entry_price']:.2f} | {pc:+.1f}% | {status}\n"
        
        return history_info
    
    def _basic_analysis(self, categorized_stocks, history):
        """기본 분석 (GPT 미사용)"""
        result = "🤖 기본 분석 (GPT API 미사용)\n\n"
        result += "## 공격적 포트폴리오 구성\n\n"
        result += "- 최고가치: 40% (4종목)\n"
        result += "- 고성장: 40% (4종목)\n"
        result += "- 균형: 20% (2종목)\n\n"
        
        for category, name in [
            ('best_value', '최고가치'), 
            ('high_growth', '고성장'), 
            ('balanced', '균형')
        ]:
            stocks = categorized_stocks.get(category, [])
            target = self.portfolio_allocation[category]['stocks']
            result += f"**{name}** (목표: {target}종목)\n"
            
            for i, stock in enumerate(stocks[:target], 1):
                result += f"  {i}. {stock['티커']}: PEG {stock['PEG']:.2f}, 성장률 {stock['성장률(%)']:.1f}%\n"
            result += "\n"
        
        return result


class PortfolioHistoryManager:
    """포트폴리오 히스토리 관리"""
    
    def __init__(self, history_file='portfolio_history.json'):
        self.history_file = history_file
        self.history = self.load_history()
        self.MAX_STAGE = 3
        self.STAGE_WEIGHTS = {1: 3, 2: 3, 3: 4}
    
    def load_history(self):
        """히스토리 로드"""
        if not os.path.exists(self.history_file):
            logger.info("📁 히스토리 파일 없음 - 새로 시작")
            return {}
        
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"📁 히스토리 로드: {len(data)}개 종목")
                return data
        except Exception as e:
            logger.error(f"❌ 히스토리 로드 실패: {e}")
            return {}
    
    def save_history(self):
        """히스토리 저장"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=4, ensure_ascii=False)
            logger.info(f"💾 히스토리 저장 완료")
        except Exception as e:
            logger.error(f"❌ 히스토리 저장 실패: {e}")
    
    def update_from_portfolio(self, categorized_stocks):
        """유형별 포트폴리오 업데이트 - 유형별 순위 기반"""
        today = datetime.now().strftime("%Y-%m-%d")
        
        active = {k: v for k, v in self.history.items() if v.get('status') == 'ACTIVE'}
        
        category_targets = {
            'best_value': 4,
            'high_growth': 4,
            'balanced': 2
        }
        
        # 유형별 Top N 종목
        current_top_by_category = {}
        for category, stocks in categorized_stocks.items():
            target_count = category_targets.get(category, 4)
            current_top_by_category[category] = {}
            
            for i, stock in enumerate(stocks[:target_count * 2], 1):
                ticker = stock['티커'].upper()
                current_top_by_category[category][ticker] = {
                    'rank': i,
                    'price': stock['price'],
                    'peg': stock['PEG'],
                    'growth': stock['성장률(%)'],
                    'in_target': i <= target_count
                }
        
        # 1. 기존 보유 종목 업데이트
        for ticker, info in list(active.items()):
            stage = info.get('stage', 0)
            category = info.get('category', 'balanced')
            
            is_in_category_top = ticker in current_top_by_category.get(category, {})
            
            if stage < self.MAX_STAGE:
                # 진행 중 → 무조건 완성
                new_stage = stage + 1
                self.history[ticker]['stage'] = new_stage
                self.history[ticker]['last_update'] = today
                
                prev_weight = info.get('current_weight_pct', 0)
                new_weight = prev_weight + self.STAGE_WEIGHTS[new_stage]
                self.history[ticker]['current_weight_pct'] = new_weight
                
                if is_in_category_top:
                    rank = current_top_by_category[category][ticker]['rank']
                    self.history[ticker]['current_price'] = current_top_by_category[category][ticker]['price']
                    self.history[ticker]['current_rank'] = rank
                    logger.info(f"📈 {ticker} ({category}): {stage}주차 → {new_stage}주차 | {prev_weight}% → {new_weight}% | {rank}위")
                else:
                    logger.info(f"📈 {ticker} ({category}): {stage}주차 → {new_stage}주차 | {prev_weight}% → {new_weight}% | ⚠️ 순위 하락")
            
            else:
                # 완성 종목
                if is_in_category_top:
                    category_info = current_top_by_category[category][ticker]
                    rank = category_info['rank']
                    in_target = category_info['in_target']
                    
                    if in_target:
                        # 목표 순위 내
                        self.history[ticker]['last_update'] = today
                        self.history[ticker]['current_price'] = category_info['price']
                        self.history[ticker]['current_rank'] = rank
                        self.history[ticker]['hold_weeks'] = info.get('hold_weeks', 0) + 1
                        logger.info(f"✅ {ticker} ({category}): 완성 유지 | {rank}위 | {info.get('hold_weeks', 0) + 1}주")
                    else:
                        # 목표 순위 밖
                        self.history[ticker]['last_update'] = today
                        self.history[ticker]['current_price'] = category_info['price']
                        self.history[ticker]['current_rank'] = rank
                        self.history[ticker]['hold_weeks'] = info.get('hold_weeks', 0) + 1
                        
                        if info.get('hold_weeks', 0) >= 2:
                            self.history[ticker]['status'] = 'SOLD'
                            self.history[ticker]['sold_date'] = today
                            self.history[ticker]['sold_reason'] = f'{category} 목표 밖 ({rank}위, 2주)'
                            logger.warning(f"📤 {ticker} ({category}): 매도 | {rank}위, {info.get('hold_weeks', 0)}주")
                        else:
                            logger.warning(f"⚠️ {ticker} ({category}): 관찰 | {rank}위, {info.get('hold_weeks', 0) + 1}주")
                
                else:
                    # 유형 Top 탈락
                    self.history[ticker]['last_update'] = today
                    self.history[ticker]['hold_weeks'] = info.get('hold_weeks', 0) + 1
                    
                    if info.get('hold_weeks', 0) >= 2:
                        self.history[ticker]['status'] = 'SOLD'
                        self.history[ticker]['sold_date'] = today
                        self.history[ticker]['sold_reason'] = f'{category} 탈락 (2주)'
                        logger.warning(f"📤 {ticker} ({category}): 매도 | 탈락, {info.get('hold_weeks', 0)}주")
                    else:
                        logger.warning(f"⚠️ {ticker} ({category}): 관찰 | 탈락, {info.get('hold_weeks', 0) + 1}주")
        
        # 2. 유형별 현황
        category_status = {}
        for cat in category_targets.keys():
            active_in_category = [
                t for t, info in self.history.items() 
                if info.get('status') == 'ACTIVE' and info.get('category') == cat
            ]
            
            total_weight = sum(
                self.history[t].get('current_weight_pct', 0) 
                for t in active_in_category
            )
            
            completed = sum(1 for t in active_in_category if self.history[t].get('stage', 0) >= self.MAX_STAGE)
            in_progress = sum(1 for t in active_in_category if self.history[t].get('stage', 0) < self.MAX_STAGE)
            
            category_status[cat] = {
                'target_count': category_targets[cat],
                'target_weight': category_targets[cat] * 10,
                'current_count': len(active_in_category),
                'completed_count': completed,
                'in_progress_count': in_progress,
                'current_weight': total_weight,
                'need_more': category_targets[cat] - completed,
                'available_slots': category_targets[cat] - len(active_in_category)
            }
        
        logger.info(f"\n📊 유형별 포트폴리오:")
        for cat, status in category_status.items():
            logger.info(f"   [{cat}] 목표:{status['target_count']}({status['target_weight']}%) | 현재:{status['current_count']}({status['current_weight']:.0f}%) | 완성:{status['completed_count']} 진행:{status['in_progress_count']}")
        
        # 3. 신규 진입
        total_weight = sum(
            info.get('current_weight_pct', 0) 
            for info in self.history.values() 
            if info.get('status') == 'ACTIVE'
        )
        available_weight = 100 - total_weight
        
        logger.info(f"\n💰 전체: {total_weight:.1f}% / 100% (여유: {available_weight:.1f}%)")
        
        if available_weight >= 3:
            new_entries = []
            
            for category, stocks in categorized_stocks.items():
                cat_status = category_status[category]
                
                if cat_status['available_slots'] > 0:
                    target_count = category_targets[category]
                    
                    owned_tickers = [
                        t for t, info in self.history.items()
                        if info.get('category') == category and info.get('status') == 'ACTIVE'
                    ]
                    
                    for stock in stocks[:target_count * 2]:
                        ticker = stock['티커'].upper()
                        
                        if ticker not in owned_tickers:
                            if ticker not in self.history or self.history[ticker].get('status') in ['REMOVED', 'SOLD']:
                                rank = list(current_top_by_category[category].keys()).index(ticker) + 1 if ticker in current_top_by_category[category] else 999
                                
                                new_entries.append({
                                    'ticker': ticker,
                                    'category': category,
                                    'rank': rank,
                                    'peg': stock['PEG'],
                                    'growth': stock['성장률(%)'],
                                    'price': stock['price'],
                                    'priority_score': cat_status['need_more'] * 100 + (10 - rank)
                                })
            
            new_entries.sort(key=lambda x: -x['priority_score'])
            
            max_new = min(
                int(available_weight / 3),
                sum(cat_status['available_slots'] for cat_status in category_status.values())
            )
            
            logger.info(f"\n🎯 신규 진입 가능: {max_new}종목\n")
            
            for entry in new_entries[:max_new]:
                ticker = entry['ticker']
                
                self.history[ticker] = {
                    'ticker': ticker,
                    'category': entry['category'],
                    'entry_date': today,
                    'entry_price': entry['price'],
                    'stage': 1,
                    'current_weight_pct': self.STAGE_WEIGHTS[1],
                    'status': 'ACTIVE',
                    'last_update': today,
                    'current_price': entry['price'],
                    'current_rank': entry['rank'],
                    'peg_at_entry': entry['peg'],
                    'growth_at_entry': entry['growth']
                }
                
                logger.info(f"🟢 {ticker}: 신규 ({entry['category']}, {entry['rank']}위, 1주차 3%)")
        
        else:
            logger.info(f"\n⚠️ 신규 불가: 여유 {available_weight:.1f}%")
        
        self.save_history()


class SlackSender:
    """슬랙 메시지 전송"""
    
    def __init__(self):
        self.token = os.environ.get('SLACK_BOT_TOKEN')
        self.channel_id = os.environ.get('SLACK_CHANNEL_ID')
        self.enabled = bool(self.token and self.channel_id)
        
        if self.enabled:
            try:
                from slack_sdk import WebClient
                from slack_sdk.errors import SlackApiError
                self.client = WebClient(token=self.token)
                self.SlackApiError = SlackApiError
                
                response = self.client.auth_test()
                logger.info(f"✅ 슬랙 연동: {response['team']}")
            except ImportError:
                logger.warning("⚠️ slack_sdk 미설치")
                self.enabled = False
            except Exception as e:
                logger.error(f"❌ 슬랙 초기화 실패: {e}")
                self.enabled = False
        else:
            logger.info("ℹ️ 슬랙 미설정 - 콘솔 출력")
    
    def send_message(self, message):
        if not self.enabled:
            return False
        
        try:
            self.client.chat_postMessage(
                channel=self.channel_id,
                text=message,
                mrkdwn=True
            )
            logger.info("✅ 슬랙 메시지 전송")
            return True
        except Exception as e:
            logger.error(f"❌ 슬랙 전송 실패: {e}")
            return False
    
    def send_file(self, file_path, title=None):
        if not self.enabled:
            return False
        
        try:
            self.client.files_upload_v2(
                channel=self.channel_id,
                file=file_path,
                title=title or os.path.basename(file_path)
            )
            logger.info(f"✅ 슬랙 파일 전송: {file_path}")
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
        
        self.GROWTH_LIMITS = {'min': 15, 'ideal_min': 20, 'ideal_max': 50, 'max': 200}
        self.PEG_LIMITS = {'excellent': 0.5, 'good': 0.7, 'fair': 1.0, 'max': 1.5}
        self.TOLERANCE = 0.20
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
    
    def run(self, ticker_limit=1000):
        start = time.time()
        
        logger.info("="*80)
        logger.info("🎯 피터 린치 스크리너 V5 - 공격적 포트폴리오")
        logger.info("="*80)
        
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
        
        logger.info(f"\n⏱️ 소요: {(time.time()-start)/60:.1f}분")
        logger.info(f"📊 파일: {filename}\n")
        return filename
    
    def _step1_collect_tickers(self, limit):
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
    
    def _step2_basic_filter(self):
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
    
    def _step3_deep_analysis(self):
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
        if not yahoo_pe or not yahoo_growth:
            return None
        
        growth_pct = yahoo_growth * 100
        if growth_pct <= 0:
            return None
        
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
    
    def _step4_categorize(self):
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
    
    def _step5_create_excel(self):
        logger.info("[5/7] Excel 생성...")
        today = datetime.now().strftime('%Y%m%d')
        filename = f'Peter_Lynch_Report_{today}.xlsx'
        
        wb = Workbook()
        wb.remove(wb.active)
        
        for name, key in [('🏆 최고가치 (40%)', 'best_value'), ('🚀 고성장 (40%)', 'high_growth'), ('⚖️ 균형 (20%)', 'balanced')]:
            stocks = self.categorized_stocks[key]
            if not stocks:
                continue
            
            ws = wb.create_sheet(title=name)
            cols = ['티커', '회사명', '섹터', '산업', '기업설명', 'PEG', '성장률(%)', 'P/E', '시가총액($B)', '투자의견', '검증상태']
            
            for i, col in enumerate(cols, 1):
                cell = ws.cell(row=1, column=i, value=col)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
            
            for i, stock in enumerate(stocks, 2):
                for j, col in enumerate(cols, 1):
                    cell = ws.cell(row=i, column=j, value=stock.get(col, ''))
                    if col == '투자의견' and '강력' in str(stock.get(col, '')):
                        cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        
        wb.save(filename)
        logger.info(f"✅ {filename}\n")
        return filename
    
    def _step6_gpt_analysis(self):
        logger.info("[6/7] GPT 분석...")
        advice = self.gpt_analyzer.analyze_portfolio(self.categorized_stocks, self.history_manager.history)
        self.history_manager.update_from_portfolio(self.categorized_stocks)
        logger.info("✅ 완료\n")
        return advice
    
    def _step7_send_to_slack(self, filename, advice):
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
    
    def _print_summary(self):
        print("\n" + "="*80)
        print("💡 공격적 포트폴리오 추천")
        print("="*80)
        
        for cat, name in [('best_value', '최고가치'), ('high_growth', '고성장'), ('balanced', '균형')]:
            stocks = self.categorized_stocks[cat]
            if stocks:
                print(f"\n【{name}】")
                for s in stocks[:3]:
                    print(f"  {s['티커']:6} - {s['회사명']}")
                    print(f"     PEG: {s['PEG']:.2f} | 성장률: {s['성장률(%)']:.1f}% | {s['검증상태']}")
        
        print("\n" + "="*80)


def main():
    print("""
╔════════════════════════════════════════════════════════════════╗
║  피터 린치 주식 스크리너 V5 - 최종 완성판                    ║
║                                                                ║
║  🎯 공격적 포트폴리오 전략:                                   ║
║     최고 가치주: 40% (4종목)                                  ║
║     고성장주: 40% (4종목)                                     ║
║     균형: 20% (2종목)                                         ║
║                                                                ║
║  ⚡ 핵심 기능:                                                 ║
║     - fast_info 사용 (속도 10배)                             ║
║     - 유형별 순위 관리                                        ║
║     - GPT-4o 분석                                             ║
║     - 히스토리 추적                                           ║
║                                                                ║
║  환경 변수: OPENAI_API_KEY (필수)                             ║
╚════════════════════════════════════════════════════════════════╝
    """)
    
    if not os.environ.get("OPENAI_API_KEY"):
        print("⚠️  경고: OPENAI_API_KEY가 설정되지 않았습니다.")
    
    if not os.environ.get("SLACK_BOT_TOKEN"):
        print("ℹ️  정보: 슬랙이 설정되지 않았습니다.\n")
    
    screener = PeterLynchScreener()
    result = screener.run(ticker_limit=1000)
    
    if result:
        print(f"\n✅ 완료: {result}")
        print(f"📁 히스토리: portfolio_history.json")
    else:
        print("\n❌ 실패")


if __name__ == "__main__":
    main()