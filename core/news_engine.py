import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import json
import datetime
from database.db_manager import get_setting, update_setting


# ─── KEYWORDS THAT AFFECT GOLD PRICE ─────────────────────────────────────────
BULLISH_KEYWORDS = [
    'inflation', 'recession', 'fed rate cut', 'interest rate cut',
    'dollar weakens', 'usd falls', 'safe haven', 'geopolitical',
    'war', 'conflict', 'crisis', 'uncertainty', 'gold demand',
    'central bank buying', 'gold rally', 'gold rises', 'gold surges'
]

BEARISH_KEYWORDS = [
    'rate hike', 'interest rate hike', 'dollar strengthens',
    'usd rises', 'risk on', 'gold falls', 'gold drops',
    'gold slips', 'profit taking', 'gold selloff'
]


# ─── FETCH NEWS ───────────────────────────────────────────────────────────────
def fetch_gold_news():
    """
    Fetches latest gold-related headlines from GNews API.
    Returns list of article dicts.
    """
    try:
        api_key = _load_news_api_key()
        if not api_key:
            print('[NewsEngine] No API key found')
            return []

        url = 'https://gnews.io/api/v4/search'
        params = {
            'q':        'gold price market',
            'lang':     'en',
            'country':  'in',
            'max':      5,
            'apikey':   api_key
        }

        r = requests.get(url, params=params, timeout=10)

        if r.status_code == 200:
            data     = r.json()
            articles = data.get('articles', [])
            print(f'[NewsEngine] Fetched {len(articles)} articles')
            return articles
        else:
            print(f'[NewsEngine] Failed: {r.status_code}')
            return []

    except Exception as e:
        print(f'[NewsEngine] Error: {e}')
        return []


def _load_news_api_key():
    try:
        base_dir     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        settings_path = os.path.join(base_dir, 'config', 'settings.json')
        with open(settings_path, 'r') as f:
            data = json.load(f)
            return data.get('gnews_api_key', '')
    except Exception:
        return ''


# ─── ANALYSE SENTIMENT ────────────────────────────────────────────────────────
def analyse_sentiment(articles):
    """
    Scans headlines for bullish/bearish gold keywords.
    Returns sentiment score and reasoning.
    """
    if not articles:
        return 0, []

    bullish_hits = []
    bearish_hits = []

    for article in articles:
        title       = article.get('title', '').lower()
        description = article.get('description', '').lower()
        text        = f"{title} {description}"

        for kw in BULLISH_KEYWORDS:
            if kw in text:
                bullish_hits.append(kw)

        for kw in BEARISH_KEYWORDS:
            if kw in text:
                bearish_hits.append(kw)

    # Score: positive = bullish, negative = bearish
    score = len(bullish_hits) - len(bearish_hits)

    return score, bullish_hits, bearish_hits


# ─── BUILD REASONING STRING ───────────────────────────────────────────────────
def build_news_reasoning(articles):
    """
    Returns a human-readable string explaining
    what's driving gold prices based on news.
    """
    if not articles:
        return None

    score, bullish, bearish = analyse_sentiment(articles)

    # Deduplicate
    bullish = list(set(bullish))[:3]
    bearish = list(set(bearish))[:3]

    if score > 0 and bullish:
        factors = ', '.join(bullish[:2])
        return f"Gold supported by {factors}"
    elif score < 0 and bearish:
        factors = ', '.join(bearish[:2])
        return f"Gold under pressure from {factors}"
    else:
        # Just return latest headline summary
        if articles:
            return f"Latest: {articles[0]['title'][:80]}"
        return None


# ─── GET LATEST HEADLINES ─────────────────────────────────────────────────────
def get_latest_headlines(articles, max_count=3):
    """Returns simplified list of headlines for UI display."""
    headlines = []
    seen_titles = set()

    for article in articles:
        title = article.get('title', '')
        if title in seen_titles:
            continue
        seen_titles.add(title)

        headlines.append({
            'title':     title,
            'source':    article.get('source', {}).get('name', ''),
            'published': article.get('publishedAt', '')[:10],
            'url':       article.get('url', '')
        })

        if len(headlines) >= max_count:
            break

    return headlines


# ─── HOURLY FETCH GATE ────────────────────────────────────────────────────────
def should_fetch_news():
    """Only fetch news once per hour to stay within free tier limits."""
    last_fetch = get_setting('news_last_fetched')
    if not last_fetch:
        return True

    try:
        last_time = datetime.datetime.fromisoformat(last_fetch)
        diff      = datetime.datetime.now() - last_time
        return diff.total_seconds() >= 3600   # 1 hour
    except Exception:
        return True


def mark_news_fetched():
    update_setting('news_last_fetched', datetime.datetime.now().isoformat())


# ─── MAIN: FETCH AND ANALYSE ──────────────────────────────────────────────────
def get_news_context(force=False):
    """
    Main function called by scheduler.
    Returns dict with reasoning and headlines.
    Only fetches if 1 hour has passed since last fetch.
    """
    if not force and not should_fetch_news():
        print('[NewsEngine] Skipping — fetched less than 1 hour ago')
        return None

    if get_setting('news_enabled') == 'off':
        return None

    articles = fetch_gold_news()

    if not articles:
        return None

    mark_news_fetched()

    reasoning  = build_news_reasoning(articles)
    headlines  = get_latest_headlines(articles)

    return {
        'reasoning':  reasoning,
        'headlines':  headlines,
        'sentiment':  analyse_sentiment(articles)[0]
    }


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('Testing news engine...\n')
    result = get_news_context(force=True)

    if result:
        print(f"Reasoning : {result['reasoning']}")
        print(f"Sentiment : {result['sentiment']}")
        print(f"\nHeadlines:")
        for h in result['headlines']:
            print(f"  [{h['source']}] {h['title']}")
            print(f"  Published: {h['published']}")
    else:
        print('No news returned — check API key in settings.json')