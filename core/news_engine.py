import sys
import os

def _project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.append(_project_root())

import requests
import json
import datetime
from database.db_manager import get_setting, update_setting


# ─── QUERY ROTATION POOL ─────────────────────────────────────────────────────
QUERY_POOL = [
    'gold price market',
    'india gold rupee MCX',
    'gold fed rates inflation',
    'gold central bank buying',
]


# ─── BULLISH / BEARISH KEYWORDS ──────────────────────────────────────────────
BULLISH_KEYWORDS = [
    'inflation', 'recession', 'fed rate cut', 'interest rate cut',
    'dollar weakens', 'usd falls', 'safe haven', 'geopolitical',
    'war', 'conflict', 'crisis', 'uncertainty', 'gold demand',
    'central bank buying', 'gold rally', 'gold rises', 'gold surges',
    'weak dollar', 'dollar weakness', 'rate cut', 'dovish',
    'gold hits', 'gold record', 'gold high'
]

BEARISH_KEYWORDS = [
    'rate hike', 'interest rate hike', 'dollar strengthens',
    'usd rises', 'risk on', 'gold falls', 'gold drops',
    'gold slips', 'profit taking', 'gold selloff', 'strong dollar',
    'dollar strength', 'hawkish', 'gold low', 'gold tumbles'
]


# ─── TIME-AWARE INTERVAL ─────────────────────────────────────────────────────
def get_fetch_interval_minutes():
    """
    Returns fetch interval based on market hours.
    MCX trades 9:00am - 11:30pm IST
    US market opens 7:30pm - 2:00am IST
    During active hours → 30 mins
    Off hours → 120 mins
    """
    now_ist = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=5, minutes=30)
    hour    = now_ist.hour
    minute  = now_ist.minute

    # MCX hours: 9:00 to 23:30 IST
    mcx_open  = (hour > 9) or (hour == 9 and minute >= 0)
    mcx_close = (hour < 23) or (hour == 23 and minute <= 30)
    in_mcx    = mcx_open and mcx_close

    # US market open: 19:30 to 02:00 IST
    in_us = hour >= 19 or hour < 2

    if in_mcx or in_us:
        return 30    # active market hours
    else:
        return 120   # off hours


def should_fetch_news():
    """Check if enough time has passed since last fetch."""
    last_fetch = get_setting('news_last_fetched')
    if not last_fetch:
        return True

    try:
        last_time    = datetime.datetime.fromisoformat(last_fetch)
        diff_minutes = (datetime.datetime.now() - last_time).total_seconds() / 60
        interval     = get_fetch_interval_minutes()
        return diff_minutes >= interval
    except Exception:
        return True


def mark_news_fetched():
    update_setting('news_last_fetched', datetime.datetime.now().isoformat())


# ─── QUERY ROTATION ───────────────────────────────────────────────────────────
def get_next_query():
    """Returns the next query in rotation and advances the counter."""
    try:
        current_idx = int(get_setting('news_query_index') or 0)
    except Exception:
        current_idx = 0

    query     = QUERY_POOL[current_idx % len(QUERY_POOL)]
    next_idx  = (current_idx + 1) % len(QUERY_POOL)
    update_setting('news_query_index', str(next_idx))

    print(f'[NewsEngine] Query rotation: [{current_idx}] "{query}"')
    return query


# ─── FETCH NEWS ───────────────────────────────────────────────────────────────
def fetch_gold_news(query=None):
    try:
        api_key = _load_news_api_key()
        if not api_key:
            print('[NewsEngine] No API key found')
            return []

        if not query:
            query = get_next_query()

        url    = 'https://gnews.io/api/v4/search'
        params = {
            'q':      query,
            'lang':   'en',
            'max':    5,
            'apikey': api_key
        }

        r = requests.get(url, params=params, timeout=10)

        if r.status_code == 200:
            data     = r.json()
            articles = data.get('articles', [])
            print(f'[NewsEngine] Fetched {len(articles)} articles for "{query}"')
            return articles
        else:
            print(f'[NewsEngine] Failed: {r.status_code}')
            return []

    except Exception as e:
        print(f'[NewsEngine] Error: {e}')
        return []


def _load_news_api_key() -> str:
    try:
        import sys
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        settings_path = os.path.join(base, 'config', 'settings.json')
        with open(settings_path, 'r') as f:
            data = json.load(f)
            return data.get('gnews_api_key', '')
    except Exception:
        return ''


# ─── DEDUPLICATION ───────────────────────────────────────────────────────────
def deduplicate_articles(articles):
    """Remove duplicates by URL and title."""
    seen_urls   = set()
    seen_titles = set()
    unique      = []

    for article in articles:
        url   = article.get('url', '')
        title = article.get('title', '').lower().strip()

        if url in seen_urls or title in seen_titles:
            continue

        seen_urls.add(url)
        seen_titles.add(title)
        unique.append(article)

    return unique


# ─── SENTIMENT CLASSIFICATION ────────────────────────────────────────────────
def classify_sentiment(articles):
    """
    Classifies market sentiment from headlines.
    Returns structured dict:
    {
        signal:     Bullish / Bearish / Neutral
        confidence: High / Medium / Low
        reason:     short explanation string
        score:      raw numeric score
    }
    """
    if not articles:
        return {
            'signal':     'Neutral',
            'confidence': 'Low',
            'reason':     'No news data',
            'score':      0
        }

    bullish_hits = []
    bearish_hits = []

    for article in articles:
        title       = article.get('title', '').lower()
        description = article.get('description', '').lower()
        text        = f"{title} {description}"

        for kw in BULLISH_KEYWORDS:
            if kw in text and kw not in bullish_hits:
                bullish_hits.append(kw)

        for kw in BEARISH_KEYWORDS:
            if kw in text and kw not in bearish_hits:
                bearish_hits.append(kw)

    score = len(bullish_hits) - len(bearish_hits)

    # Signal
    if score > 0:
        signal = 'Bullish'
    elif score < 0:
        signal = 'Bearish'
    else:
        signal = 'Neutral'

    # Confidence
    total_hits = len(bullish_hits) + len(bearish_hits)
    if total_hits >= 4:
        confidence = 'High'
    elif total_hits >= 2:
        confidence = 'Medium'
    else:
        confidence = 'Low'

    # Reason — pick most impactful keyword
    if signal == 'Bullish' and bullish_hits:
        reason = bullish_hits[0].title()
    elif signal == 'Bearish' and bearish_hits:
        reason = bearish_hits[0].title()
    else:
        reason = 'Mixed signals'

    return {
        'signal':     signal,
        'confidence': confidence,
        'reason':     reason,
        'score':      score
    }


# ─── BUILD REASONING STRING ──────────────────────────────────────────────────
def build_news_reasoning(articles, sentiment):
    if not articles:
        return None

    if sentiment['signal'] == 'Bullish':
        return f"Gold supported by {sentiment['reason'].lower()}"
    elif sentiment['signal'] == 'Bearish':
        return f"Gold under pressure from {sentiment['reason'].lower()}"
    else:
        if articles:
            return f"Mixed signals — {articles[0]['title'][:60]}"
        return None


# ─── GET HEADLINES ────────────────────────────────────────────────────────────
def get_latest_headlines(articles, max_count=3):
    unique = deduplicate_articles(articles)
    headlines = []

    for article in unique[:max_count]:
        headlines.append({
            'title':     article.get('title', ''),
            'source':    article.get('source', {}).get('name', ''),
            'published': article.get('publishedAt', '')[:10],
            'url':       article.get('url', '')
        })

    return headlines


# ─── MAIN FUNCTION ────────────────────────────────────────────────────────────
def get_news_context(force=False):
    if not force and not should_fetch_news():
        interval = get_fetch_interval_minutes()
        print(f'[NewsEngine] Skipping — interval is {interval} mins (market hours aware)')
        return None

    if get_setting('news_enabled') == 'off':
        return None

    articles  = fetch_gold_news()

    if not articles:
        return None

    mark_news_fetched()

    articles   = deduplicate_articles(articles)
    sentiment  = classify_sentiment(articles)
    reasoning  = build_news_reasoning(articles, sentiment)
    headlines  = get_latest_headlines(articles)

    return {
        'reasoning':  reasoning,
        'headlines':  headlines,
        'sentiment':  sentiment,
    }


# ─── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('Testing news engine...\n')

    now_ist = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=5, minutes=30)
    interval = get_fetch_interval_minutes()
    print(f'Current IST: {now_ist.strftime("%H:%M")}')
    print(f'Fetch interval: {interval} minutes')
    print(f'Next query: {QUERY_POOL[int(get_setting("news_query_index") or 0) % len(QUERY_POOL)]}')

    print('\nFetching...')
    result = get_news_context(force=True)

    if result:
        s = result['sentiment']
        print(f"\nSentiment : {s['signal']} ({s['confidence']} confidence)")
        print(f"Reason    : {s['reason']}")
        print(f"Score     : {s['score']}")
        print(f"Reasoning : {result['reasoning']}")
        print(f"\nHeadlines:")
        for h in result['headlines']:
            print(f"  [{h['source']}] {h['title']}")
    else:
        print('No news returned')