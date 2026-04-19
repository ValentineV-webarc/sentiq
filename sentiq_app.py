from flask import Flask, render_template, request, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
from newsapi import NewsApiClient
import requests as http_requests
import pandas as pd
from scipy import stats
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER
import io, json, secrets, os
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'sentiq-secret-key-change-in-production')

database_url = os.environ.get('DATABASE_URL', 'sqlite:///sentiq.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['MAIL_SERVER']        = 'smtp.gmail.com'
app.config['MAIL_PORT']          = 587
app.config['MAIL_USE_TLS']       = True
app.config['MAIL_USERNAME']      = os.environ.get('MAIL_USERNAME', 'sentiq.alerts@gmail.com')
app.config['MAIL_PASSWORD']      = os.environ.get('MAIL_PASSWORD', 'ddbvghvazhplcfnz')
app.config['MAIL_DEFAULT_SENDER']= ('SentIQ', os.environ.get('MAIL_USERNAME', 'sentiq.alerts@gmail.com'))

db            = SQLAlchemy(app)
login_manager = LoginManager(app)
mail          = Mail(app)

API_KEY = os.environ.get('NEWS_API_KEY', '9a0f711450fd4bffb78ef899c2c85564')
APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions'

# ── Models ────────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    email      = db.Column(db.String(120), unique=True, nullable=False)
    name       = db.Column(db.String(80), nullable=False)
    password   = db.Column(db.String(200), nullable=False)
    history    = db.relationship('SearchHistory', backref='user', lazy=True, cascade='all, delete-orphan')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SearchHistory(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    brands     = db.Column(db.Text, nullable=False)
    limit      = db.Column(db.Integer, default=50)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    alert      = db.relationship('Alert', backref='history', uselist=False, cascade='all, delete-orphan')

class Alert(db.Model):
    id             = db.Column(db.Integer, primary_key=True)
    history_id     = db.Column(db.Integer, db.ForeignKey('search_history.id'), nullable=False)
    alert_email    = db.Column(db.String(120), nullable=False)
    threshold      = db.Column(db.Float, nullable=False)
    active         = db.Column(db.Boolean, default=True)
    last_checked   = db.Column(db.DateTime, nullable=True)
    last_triggered = db.Column(db.DateTime, nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

class PasswordReset(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token      = db.Column(db.String(100), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used       = db.Column(db.Boolean, default=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── Sentiment model ───────────────────────────────────────────────────────────

def simple_sentiment(text):
    """Rule-based sentiment using keyword matching — no external API needed."""
    if not text or str(text) == 'None':
        return 'NEUTRAL', 0.5

    text_lower = str(text).lower()

    positive_words = [
        'good', 'great', 'excellent', 'amazing', 'awesome', 'best', 'win', 'winning',
        'success', 'successful', 'top', 'leading', 'impressive', 'strong', 'powerful',
        'innovative', 'advanced', 'faster', 'better', 'improve', 'improved', 'launch',
        'launches', 'new', 'breakthrough', 'record', 'growth', 'profit', 'rise', 'rises',
        'positive', 'love', 'perfect', 'outstanding', 'superb', 'brilliant', 'efficient',
        'reliable', 'recommend', 'recommended', 'worth', 'upgrade', 'upgraded', 'boost',
        'boosted', 'gains', 'gain', 'surge', 'surges', 'soars', 'soar', 'beats', 'beat',
        'exceeds', 'exceed', 'outperforms', 'exciting', 'pleased', 'happy', 'satisfied'
    ]

    negative_words = [
        'bad', 'worst', 'terrible', 'awful', 'poor', 'weak', 'fail', 'fails', 'failed',
        'failure', 'problem', 'problems', 'issue', 'issues', 'bug', 'bugs', 'crash',
        'crashes', 'slow', 'slower', 'delay', 'delayed', 'behind', 'disappointing',
        'disappointed', 'concern', 'concerns', 'risk', 'risks', 'loss', 'losses', 'drop',
        'drops', 'drops', 'decline', 'declines', 'fell', 'fall', 'falls', 'miss', 'misses',
        'missed', 'below', 'struggle', 'struggles', 'struggling', 'lawsuit', 'ban', 'banned',
        'hack', 'hacked', 'breach', 'vulnerable', 'vulnerability', 'recall', 'recalled',
        'layoff', 'layoffs', 'cut', 'cuts', 'overheating', 'expensive', 'costly', 'waste'
    ]

    pos_count = sum(1 for w in positive_words if w in text_lower)
    neg_count = sum(1 for w in negative_words if w in text_lower)

    total = pos_count + neg_count
    if total == 0:
        return 'NEUTRAL', 0.5

    if pos_count > neg_count:
        score = round(0.5 + (pos_count / total) * 0.5, 3)
        return 'POSITIVE', min(score, 0.99)
    elif neg_count > pos_count:
        score = round(0.5 + (neg_count / total) * 0.5, 3)
        return 'NEGATIVE', min(score, 0.99)
    else:
        return 'NEUTRAL', 0.5

def analyse_sentiment_batch(texts):
    return [simple_sentiment(t) for t in texts]

def analyse_sentiment(text):
    return simple_sentiment(text)

def run_sentiment_for_brands(brands, limit=50):
    newsapi = NewsApiClient(api_key=API_KEY)
    all_articles = []
    for brand in brands:
        try:
            articles = newsapi.get_everything(q=brand, language='en',
                sort_by='publishedAt', page_size=limit)
            for a in articles['articles']:
                all_articles.append({'brand': brand, 'title': a['title'], 'description': a['description']})
        except:
            pass
    if not all_articles:
        return {}
    df = pd.DataFrame(all_articles)
    df['text'] = df['title'].fillna('') + ' ' + df['description'].fillna('')
    results = df['text'].apply(analyse_sentiment)
    df['sentiment'] = [r[0] for r in results]
    summary = df.groupby(['brand', 'sentiment']).size().reset_index(name='count')
    summary['percentage'] = summary.groupby('brand')['count'].transform(lambda x: round(x / x.sum() * 100, 1))
    kpis = {}
    for brand in brands:
        pos = summary[(summary['brand'] == brand) & (summary['sentiment'] == 'POSITIVE')]['percentage'].values
        neg = summary[(summary['brand'] == brand) & (summary['sentiment'] == 'NEGATIVE')]['percentage'].values
        kpis[brand] = {
            'positive': float(pos[0]) if len(pos) > 0 else 0.0,
            'negative': float(neg[0]) if len(neg) > 0 else 0.0,
            'total':    len(df[df['brand'] == brand])
        }
    return kpis

# ── Email helpers ─────────────────────────────────────────────────────────────

def send_alert_email(alert, history, kpis, breached_brands):
    try:
        brand_lines = ''.join([f"""
            <tr>
              <td style="padding:10px 16px;border-bottom:1px solid #E4E1DA">{brand}</td>
              <td style="padding:10px 16px;border-bottom:1px solid #E4E1DA;color:#991B1B;font-weight:600">{kpis[brand]['positive']:.1f}%</td>
              <td style="padding:10px 16px;border-bottom:1px solid #E4E1DA;color:#991B1B">Below {alert.threshold:.0f}% threshold</td>
            </tr>""" for brand in breached_brands])
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:580px;margin:0 auto;color:#18170F">
          <div style="background:#1B3A6B;padding:24px 32px;border-radius:8px 8px 0 0">
            <h1 style="color:white;margin:0;font-size:22px">SentIQ</h1>
            <p style="color:#9BB3D4;margin:4px 0 0;font-size:13px">Brand Sentiment Alert</p>
          </div>
          <div style="background:#fff;border:1px solid #E4E1DA;border-top:none;padding:28px 32px;border-radius:0 0 8px 8px">
            <h2 style="margin:0 0 8px;font-size:18px">⚠️ Sentiment threshold breached</h2>
            <p style="color:#6B6860;margin:0 0 20px;font-size:14px">
              Brands dropped below your <strong>{alert.threshold:.0f}% positive</strong> threshold.
            </p>
            <table style="width:100%;border-collapse:collapse;border:1px solid #E4E1DA;margin-bottom:20px">
              <thead><tr style="background:#F7F6F3">
                <th style="padding:10px 16px;text-align:left;font-size:11px;text-transform:uppercase;color:#6B6860">Brand</th>
                <th style="padding:10px 16px;text-align:left;font-size:11px;text-transform:uppercase;color:#6B6860">Positive %</th>
                <th style="padding:10px 16px;text-align:left;font-size:11px;text-transform:uppercase;color:#6B6860">Status</th>
              </tr></thead>
              <tbody>{brand_lines}</tbody>
            </table>
            <p style="color:#6B6860;font-size:13px;margin:0">
              Brands monitored: <strong>{', '.join(json.loads(history.brands))}</strong><br>
              Checked: {datetime.now().strftime('%d %B %Y at %H:%M')}
            </p>
            <div style="margin-top:24px;padding-top:20px;border-top:1px solid #E4E1DA;font-size:12px;color:#6B6860">
              Sent by SentIQ · To stop alerts, log in and remove the alert from your search history.
            </div>
          </div>
        </div>"""
        msg = Message(subject=f"SentIQ Alert — sentiment dropped below {alert.threshold:.0f}%",
                      recipients=[alert.alert_email], html=html)
        mail.send(msg)
        return True
    except Exception as e:
        print(f"[Alert] Email failed: {e}")
        return False

def send_reset_email(user, token):
    reset_url = f"{APP_URL}/reset-password/{token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto">
      <div style="background:#1B3A6B;padding:20px 28px;border-radius:8px 8px 0 0">
        <h2 style="color:white;margin:0;font-size:18px">SentIQ</h2>
      </div>
      <div style="border:1px solid #E4E1DA;border-top:none;padding:28px;border-radius:0 0 8px 8px">
        <h3 style="margin:0 0 8px">Reset your password</h3>
        <p style="color:#6B6860;font-size:14px;margin:0 0 20px">
          Hi {user.name}, click the button below to reset your password.<br>
          This link expires in <strong>1 hour</strong>.
        </p>
        <a href="{reset_url}" style="display:inline-block;padding:12px 24px;background:#1B3A6B;color:white;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600">
          Reset password
        </a>
        <p style="color:#6B6860;font-size:12px;margin-top:20px">
          If you didn't request this, ignore this email. Your password won't change.
        </p>
      </div>
    </div>"""
    msg = Message(subject="SentIQ — Reset your password",
                  recipients=[user.email], html=html)
    mail.send(msg)

# ── Scheduler ─────────────────────────────────────────────────────────────────

def check_all_alerts():
    with app.app_context():
        alerts = Alert.query.filter_by(active=True).all()
        if not alerts:
            return
        print(f"[Scheduler] Checking {len(alerts)} alert(s)...")
        for alert in alerts:
            if alert.last_checked and datetime.utcnow() - alert.last_checked < timedelta(hours=23):
                continue
            history = SearchHistory.query.get(alert.history_id)
            if not history:
                continue
            brands = json.loads(history.brands)
            kpis   = run_sentiment_for_brands(brands, history.limit)
            if not kpis:
                continue
            breached = [b for b in brands if kpis.get(b, {}).get('positive', 100) < alert.threshold]
            alert.last_checked = datetime.utcnow()
            db.session.commit()
            if breached:
                sent = send_alert_email(alert, history, kpis, breached)
                if sent:
                    alert.last_triggered = datetime.utcnow()
                    db.session.commit()

def generate_insights(brands, kpis, articles, trend_data=None, ab_test=None):
    """Use Groq LLaMA to generate a clear, chart-summarising insight.

    The goal is a short plain-English verdict that reads the dashboard for the
    user: who's ahead, by how much, whether the gap is meaningful, and which
    way each brand is trending — not a list of article titles.
    """
    if not GROQ_API_KEY:
        return None
    try:
        # 1) Brand headline stats (from the bar chart)
        brand_stats = []
        for brand in brands:
            k = kpis.get(brand, {})
            brand_stats.append(
                f"{brand}: {k.get('positive', 0):.1f}% positive · "
                f"{k.get('negative', 0):.1f}% negative "
                f"({k.get('total', 0)} articles)"
            )

        # 2) Quick read of each brand's trend line (from the time-series chart)
        trend_lines = []
        if trend_data:
            for brand in brands:
                t = trend_data.get(brand, {})
                scores = t.get('scores', []) or []
                dates  = t.get('dates', [])  or []
                if len(scores) >= 2:
                    first, last = scores[0] * 100, scores[-1] * 100
                    hi, lo = max(scores) * 100, min(scores) * 100
                    delta = last - first
                    if delta >= 10:    direction = "rising"
                    elif delta <= -10: direction = "falling"
                    elif hi - lo >= 25: direction = "volatile"
                    else:              direction = "flat"
                    trend_lines.append(
                        f"{brand}: {direction} — started {first:.0f}%, ended {last:.0f}% "
                        f"(range {lo:.0f}%–{hi:.0f}% across {len(scores)} days, {dates[0]} → {dates[-1]})"
                    )
                elif len(scores) == 1:
                    trend_lines.append(f"{brand}: only one day of data ({dates[0]}) at {scores[0]*100:.0f}% positive")

        # 3) Statistical test verdict (plain English)
        ab_line = ""
        if ab_test:
            if ab_test.get('significant'):
                ab_line = f"A/B t-test p = {ab_test.get('p_value')} — the gap IS statistically meaningful."
            else:
                ab_line = f"A/B t-test p = {ab_test.get('p_value')} — the gap is NOT statistically meaningful, so treat the brands as roughly tied."

        # 4) A small article sample — context only, never to be listed
        article_lines = []
        for a in articles[:10]:
            article_lines.append(f"- [{a.get('brand')}] {a.get('title')} ({a.get('sentiment','').lower()})")

        prompt = f"""You are writing a short plain-English summary that sits above a sentiment-comparison dashboard. The reader has just glanced at two charts:
  (1) a bar chart of positive vs negative sentiment per brand (overall share across the whole period)
  (2) a stacked bar chart of daily coverage per brand, broken down into positive / neutral / negative article counts

Your job is to summarise WHAT THE CHARTS SHOW — a headline verdict, not a roll-call of articles.

Write exactly 3–4 short sentences, flowing prose, no bullets, no headers, no markdown. Follow this order:
  1. Lead with the verdict: which brand is ahead on positive sentiment and by how many percentage points. If within ~3 points, say "roughly tied".
  2. State whether the gap is meaningful, using the statistical test result in plain words (don't quote t-values).
  3. Describe the daily pattern for each brand using the EXACT direction word given in the "Trend over time" section below (rising, falling, flat, or volatile) — do NOT substitute your own adjectives. Anchor the description with start and end numbers. Never call a pattern "volatile", "dramatic", or "significant strides" unless the data block below literally says "volatile".
  4. Close with ONE sentence naming a likely theme behind the sentiment (drawn loosely from the article examples). Do NOT list article titles.

Tone: punchy, jargon-free, confident. A busy reader must grasp the dashboard in under 15 seconds from your summary alone. Never open with "Our analysis reveals", "This report shows", or any similar filler — start with the verdict itself.

— Brand headlines (overall bar chart) —
{chr(10).join(brand_stats)}

— Trend over time (daily coverage chart) —
{chr(10).join(trend_lines) if trend_lines else "(not enough time-series data)"}

— Statistical test —
{ab_line or "(not available)"}

— Article examples (for theme context only; do NOT list them) —
{chr(10).join(article_lines)}

Write the summary now:"""

        response = http_requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 220,
                "temperature": 0.5
            },
            timeout=20
        )
        if response.status_code == 200:
            data = response.json()
            return data['choices'][0]['message']['content'].strip()
        else:
            print(f"[Groq] Error {response.status_code}: {response.text[:200]}")
        return None
    except Exception as e:
        print(f"[Groq] Error: {e}")
        return None

scheduler = BackgroundScheduler()
scheduler.add_job(check_all_alerts, 'interval', hours=1, id='alert_check')

# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/reset-password/<token>')
def reset_password_page(token):
    pr = PasswordReset.query.filter_by(token=token, used=False).first()
    if not pr or pr.expires_at < datetime.utcnow():
        return render_template('index.html')
    return render_template('index.html', reset_token=token)

# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/auth/register', methods=['POST'])
def register():
    data     = request.get_json()
    name     = (data.get('name') or '').strip()
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not name or not email or not password:
        return jsonify({'error': 'All fields are required.'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'An account with that email already exists.'}), 400
    user = User(name=name, email=email, password=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    login_user(user, remember=True)
    return jsonify({'ok': True, 'name': user.name, 'email': user.email})

@app.route('/auth/login', methods=['POST'])
def login():
    data     = request.get_json()
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password, password):
        return jsonify({'error': 'Incorrect email or password.'}), 401
    login_user(user, remember=True)
    return jsonify({'ok': True, 'name': user.name, 'email': user.email})

@app.route('/auth/logout', methods=['POST'])
def logout():
    logout_user()
    return jsonify({'ok': True})

@app.route('/auth/me')
def me():
    if current_user.is_authenticated:
        return jsonify({'logged_in': True, 'name': current_user.name, 'email': current_user.email})
    return jsonify({'logged_in': False})

@app.route('/auth/forgot-password', methods=['POST'])
def forgot_password():
    data  = request.get_json()
    email = (data.get('email') or '').strip().lower()
    user  = User.query.filter_by(email=email).first()
    # Always return success to avoid revealing if email exists
    if user:
        token = secrets.token_urlsafe(32)
        pr = PasswordReset(user_id=user.id, token=token,
                           expires_at=datetime.utcnow() + timedelta(hours=1))
        db.session.add(pr)
        db.session.commit()
        try:
            send_reset_email(user, token)
        except Exception as e:
            print(f"[Reset] Email failed: {e}")
    return jsonify({'ok': True})

@app.route('/auth/reset-password', methods=['POST'])
def reset_password():
    data     = request.get_json()
    token    = data.get('token') or ''
    password = data.get('password') or ''
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters.'}), 400
    pr = PasswordReset.query.filter_by(token=token, used=False).first()
    if not pr or pr.expires_at < datetime.utcnow():
        return jsonify({'error': 'This reset link has expired. Please request a new one.'}), 400
    user = User.query.get(pr.user_id)
    user.password = generate_password_hash(password)
    pr.used = True
    db.session.commit()
    login_user(user, remember=True)
    return jsonify({'ok': True, 'name': user.name, 'email': user.email})

# ── Search history ────────────────────────────────────────────────────────────

@app.route('/api/history', methods=['GET'])
@login_required
def get_history():
    items = SearchHistory.query.filter_by(user_id=current_user.id)\
              .order_by(SearchHistory.created_at.desc()).limit(20).all()
    return jsonify([{
        'id':         h.id,
        'brands':     json.loads(h.brands),
        'limit':      h.limit,
        'created_at': h.created_at.strftime('%d %b %Y, %H:%M'),
        'alert': {
            'id':             h.alert.id,
            'threshold':      h.alert.threshold,
            'alert_email':    h.alert.alert_email,
            'active':         h.alert.active,
            'last_triggered': h.alert.last_triggered.strftime('%d %b %Y %H:%M') if h.alert.last_triggered else None
        } if h.alert else None
    } for h in items])

@app.route('/api/history', methods=['POST'])
@login_required
def add_history():
    data   = request.get_json()
    brands = data.get('brands', [])
    limit  = data.get('limit', 50)
    # Avoid duplicate consecutive entries
    last = SearchHistory.query.filter_by(user_id=current_user.id)\
             .order_by(SearchHistory.created_at.desc()).first()
    if last and json.loads(last.brands) == brands:
        return jsonify({'id': last.id, 'brands': brands, 'limit': last.limit,
                        'created_at': last.created_at.strftime('%d %b %Y, %H:%M'), 'alert': None})
    h = SearchHistory(user_id=current_user.id, brands=json.dumps(brands), limit=limit)
    db.session.add(h)
    db.session.commit()
    return jsonify({'id': h.id, 'brands': brands, 'limit': h.limit,
                    'created_at': h.created_at.strftime('%d %b %Y, %H:%M'), 'alert': None})

@app.route('/api/history/<int:history_id>', methods=['DELETE'])
@login_required
def delete_history(history_id):
    h = SearchHistory.query.filter_by(id=history_id, user_id=current_user.id).first()
    if not h:
        return jsonify({'error': 'Not found.'}), 404
    db.session.delete(h)
    db.session.commit()
    return jsonify({'ok': True})

# ── Alert routes ──────────────────────────────────────────────────────────────

@app.route('/api/history/<int:history_id>/alert', methods=['POST'])
@login_required
def set_alert(history_id):
    h = SearchHistory.query.filter_by(id=history_id, user_id=current_user.id).first()
    if not h:
        return jsonify({'error': 'Not found.'}), 404
    data      = request.get_json()
    email     = (data.get('alert_email') or '').strip()
    threshold = data.get('threshold', 40)
    if not email:
        return jsonify({'error': 'Please enter an email address.'}), 400
    if h.alert:
        h.alert.alert_email = email
        h.alert.threshold   = threshold
        h.alert.active      = True
    else:
        alert = Alert(history_id=h.id, alert_email=email, threshold=threshold)
        db.session.add(alert)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/history/<int:history_id>/alert', methods=['DELETE'])
@login_required
def delete_alert(history_id):
    h = SearchHistory.query.filter_by(id=history_id, user_id=current_user.id).first()
    if not h or not h.alert:
        return jsonify({'error': 'Not found.'}), 404
    db.session.delete(h.alert)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/history/<int:history_id>/alert/test', methods=['POST'])
@login_required
def test_alert(history_id):
    h = SearchHistory.query.filter_by(id=history_id, user_id=current_user.id).first()
    if not h or not h.alert:
        return jsonify({'error': 'Alert not found.'}), 404
    try:
        msg = Message(
            subject='SentIQ — Test Alert ✓',
            recipients=[h.alert.alert_email],
            html=f"""
            <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto">
              <div style="background:#1B3A6B;padding:20px 28px;border-radius:8px 8px 0 0">
                <h2 style="color:white;margin:0;font-size:18px">SentIQ</h2>
              </div>
              <div style="border:1px solid #E4E1DA;border-top:none;padding:24px 28px;border-radius:0 0 8px 8px">
                <h3 style="margin:0 0 8px">✅ Test alert successful!</h3>
                <p style="color:#6B6860;font-size:14px;margin:0">
                  Your alert for <strong>{', '.join(json.loads(h.brands))}</strong> is configured.<br>
                  You'll be notified when positive sentiment drops below <strong>{h.alert.threshold:.0f}%</strong>.
                </p>
              </div>
            </div>"""
        )
        mail.send(msg)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Analysis ──────────────────────────────────────────────────────────────────

@app.route('/api/analyse', methods=['POST'])
def analyse():
    data   = request.get_json()
    brands = data.get('brands', [])
    limit  = data.get('limit', 50)
    if len(brands) < 2:
        return jsonify({'error': 'Please enter at least 2 brands.'}), 400

    # ── Resolve the date window ──────────────────────────────────────────────
    # Users can either pick a preset (days=3/7/14/30) or supply a custom
    # from/to range. Default is last 7 days, which gives a meaningful trend
    # without burning through the NewsAPI free tier's 30-day history limit.
    today         = datetime.utcnow().date()
    from_param    = (data.get('from') or '').strip()
    to_param      = (data.get('to')   or '').strip()
    days_param    = data.get('days', 7)

    if from_param and to_param:
        # Custom range — validate the strings are YYYY-MM-DD
        try:
            from_date = datetime.strptime(from_param, '%Y-%m-%d').date()
            to_date   = datetime.strptime(to_param,   '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Dates must be in YYYY-MM-DD format.'}), 400
        if from_date > to_date:
            return jsonify({'error': 'Start date must be before end date.'}), 400
        if to_date > today:
            return jsonify({'error': 'End date cannot be in the future.'}), 400
        if (today - from_date).days > 30:
            return jsonify({'error': 'NewsAPI free tier only returns articles from the last 30 days. Please pick a start date within that window.'}), 400
    else:
        # Preset — clamp to the NewsAPI free-tier ceiling
        try:
            days = int(days_param)
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(30, days))
        to_date   = today
        from_date = today - timedelta(days=days - 1)

    from_str = from_date.strftime('%Y-%m-%d')
    to_str   = to_date.strftime('%Y-%m-%d')

    # One NewsAPI call per brand for the full date window. NewsAPI clusters
    # results near the present regardless of the older boundary — chunking
    # the window doesn't meaningfully change that, it just burns 3× the
    # daily quota. The KPI cards flag "limited coverage" when articles end
    # up concentrated in a few days, which is the honest signal to show.
    newsapi = NewsApiClient(api_key=API_KEY)
    all_articles = []
    for brand in brands:
        try:
            articles = newsapi.get_everything(
                q=brand, language='en',
                sort_by='publishedAt',
                from_param=from_str, to=to_str,
                page_size=min(int(limit), 100)    # NewsAPI free-tier per-request cap
            )
            seen_urls = set()
            for article in articles.get('articles', []):
                url = article.get('url')
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                all_articles.append({
                    'brand':        brand,
                    'title':        article['title'],
                    'description':  article['description'],
                    'published_at': article['publishedAt'],
                    'source':       article['source']['name'],
                    'url':          article['url']
                })
        except Exception as e:
            print(f"[NewsAPI] Failed for {brand}: {e}")
            continue

    if not all_articles:
        return jsonify({'error': f'No articles found between {from_str} and {to_str}. Try different brand names or a wider date range.'}), 400

    df = pd.DataFrame(all_articles)
    df['text'] = df['title'].fillna('') + ' ' + df['description'].fillna('')

    # Batch sentiment analysis — send all texts at once
    BATCH_SIZE = 32
    texts = df['text'].tolist()
    all_results = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i+BATCH_SIZE]
        all_results.extend(analyse_sentiment_batch(batch))

    df['sentiment']       = [r[0] for r in all_results]
    df['confidence']      = [r[1] for r in all_results]
    df['sentiment_score'] = (df['sentiment'] == 'POSITIVE').astype(int)

    summary = df.groupby(['brand', 'sentiment']).size().reset_index(name='count')
    summary['percentage'] = summary.groupby('brand')['count'].transform(lambda x: round(x / x.sum() * 100, 1))

    df['date'] = pd.to_datetime(df['published_at']).dt.strftime('%Y-%m-%d')

    # Daily stats per (brand, date): raw positive-rate, article count, and a
    # per-sentiment breakdown. The breakdown (pos/neu/neg counts per day) is
    # what the frontend stacked bar chart renders — each bar is a single day,
    # segmented by sentiment category.
    daily = df.groupby(['date', 'brand']).agg(
        raw_score=('sentiment_score', 'mean'),
        count=('sentiment_score', 'size')
    ).reset_index()

    # Pivot sentiment counts: one row per (date, brand), columns = sentiment cats.
    cat_counts = (df.groupby(['date', 'brand', 'sentiment']).size()
                    .unstack(fill_value=0)
                    .reset_index())
    # Make sure all three columns exist even if one sentiment was absent
    for col in ('POSITIVE', 'NEUTRAL', 'NEGATIVE'):
        if col not in cat_counts.columns:
            cat_counts[col] = 0
    daily = daily.merge(cat_counts[['date','brand','POSITIVE','NEUTRAL','NEGATIVE']],
                        on=['date','brand'], how='left').fillna(0)

    # Per-brand: sort by date, then do a 3-day centered rolling mean weighted
    # by article counts — i.e. sum(score * count) / sum(count) over the window.
    # Weighting by count means a day with 10 articles influences the smoothed
    # line far more than a day with 1 article, which is what we want.
    smoothed_parts = []
    for brand in brands:
        b = daily[daily['brand'] == brand].sort_values('date').copy()
        if len(b) == 0:
            continue
        weighted = (b['raw_score'] * b['count']).rolling(window=3, min_periods=1, center=True).sum()
        weights  = b['count'].rolling(window=3, min_periods=1, center=True).sum()
        b['sentiment_score'] = (weighted / weights).round(3)
        smoothed_parts.append(b)
    trend = pd.concat(smoothed_parts) if smoothed_parts else daily.assign(sentiment_score=daily['raw_score'])

    kpis = {}
    for brand in brands:
        pos = summary[(summary['brand'] == brand) & (summary['sentiment'] == 'POSITIVE')]['percentage'].values
        neg = summary[(summary['brand'] == brand) & (summary['sentiment'] == 'NEGATIVE')]['percentage'].values
        brand_df = df[df['brand'] == brand]
        kpis[brand] = {
            'positive':            float(pos[0]) if len(pos) > 0 else 0.0,
            'negative':            float(neg[0]) if len(neg) > 0 else 0.0,
            'total':               len(brand_df),
            # How many distinct calendar days had at least one article for this brand.
            # Lets the frontend show "N articles across M days" so viewers can tell
            # whether a brand has broad coverage or is clustered on just a day or two.
            'days_with_articles':  int(brand_df['date'].nunique()) if len(brand_df) else 0
        }

    ab_test = None
    if len(brands) == 2:
        scores_a = (df[df['brand'] == brands[0]]['sentiment'] == 'POSITIVE').astype(int).values
        scores_b = (df[df['brand'] == brands[1]]['sentiment'] == 'POSITIVE').astype(int).values
        t_stat, p_value = stats.ttest_ind(scores_a, scores_b)
        ab_test = {'t_stat': round(float(t_stat),3), 'p_value': round(float(p_value),4),
                   'significant': bool(p_value < 0.05)}

    top_articles = df[['brand','title','sentiment','confidence','source','url','published_at','description','date']]\
        .sort_values('confidence', ascending=False).head(50).to_dict('records')

    trend_data = {}
    for brand in brands:
        t = trend[trend['brand'] == brand]
        trend_data[brand] = {
            'dates':      t['date'].tolist(),
            'scores':     t['sentiment_score'].tolist(),   # smoothed (3-day weighted)
            'raw_scores': t['raw_score'].round(3).tolist(), # raw daily rate, kept for backward compat
            'counts':     t['count'].astype(int).tolist(),  # total articles per day
            # Per-day category counts — drive the stacked bar chart
            'pos_counts': t['POSITIVE'].astype(int).tolist(),
            'neu_counts': t['NEUTRAL'].astype(int).tolist(),
            'neg_counts': t['NEGATIVE'].astype(int).tolist()
        }

    # Auto-save to history if logged in
    if current_user.is_authenticated:
        last = SearchHistory.query.filter_by(user_id=current_user.id)\
                 .order_by(SearchHistory.created_at.desc()).first()
        if not last or json.loads(last.brands) != brands:
            h = SearchHistory(user_id=current_user.id, brands=json.dumps(brands), limit=limit)
            db.session.add(h)
            db.session.commit()

    # Generate LLM insight — pass the trend + A/B result so the summary
    # can actually read the charts, not just rattle off article titles.
    insight = generate_insights(brands, kpis, top_articles,
                                trend_data=trend_data, ab_test=ab_test)

    return jsonify({'total_articles': len(df), 'brands': brands, 'kpis': kpis,
                    'ab_test': ab_test, 'trend': trend_data, 'articles': top_articles,
                    'insight': insight,
                    'date_range': {'from': from_str, 'to': to_str,
                                   'days': (to_date - from_date).days + 1}})

# ── PDF Export ────────────────────────────────────────────────────────────────

@app.route('/api/export/pdf', methods=['POST'])
def export_pdf():
    data           = request.get_json()
    brands         = data.get('brands', [])
    kpis           = data.get('kpis', {})
    ab_test        = data.get('ab_test', None)
    articles       = data.get('articles', [])
    total_articles = data.get('total_articles', 0)

    buffer = io.BytesIO()
    NAVY   = colors.HexColor('#1B3A6B'); LIGHT  = colors.HexColor('#EEF2F9')
    BORDER = colors.HexColor('#E4E1DA'); MUTED  = colors.HexColor('#6B6860')
    POS_CLR= colors.HexColor('#166534'); NEG_CLR= colors.HexColor('#991B1B')
    POS_BG = colors.HexColor('#F0FDF4'); NEG_BG = colors.HexColor('#FEF2F2')
    WHITE  = colors.white; BLACK = colors.HexColor('#18170F')

    doc  = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    h1   = ParagraphStyle('h1',   fontName='Helvetica-Bold', fontSize=20, textColor=NAVY, spaceAfter=4, leading=24)
    h2   = ParagraphStyle('h2',   fontName='Helvetica-Bold', fontSize=12, textColor=NAVY, spaceAfter=6, spaceBefore=16, leading=16)
    body = ParagraphStyle('body', fontName='Helvetica', fontSize=9, textColor=BLACK, leading=13)
    muted= ParagraphStyle('muted',fontName='Helvetica', fontSize=8, textColor=MUTED, leading=11)
    ctr  = ParagraphStyle('ctr',  fontName='Helvetica', fontSize=9, alignment=TA_CENTER, textColor=MUTED)

    story = []
    story.append(Paragraph('SentIQ', h1))
    story.append(Paragraph('Brand Sentiment Intelligence Report', muted))
    story.append(Spacer(1, 4))
    story.append(Paragraph(f'Generated: {datetime.now().strftime("%d %B %Y, %H:%M")}  ·  Brands: {" vs ".join(brands)}  ·  {total_articles} articles analysed', muted))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width='100%', thickness=1, color=BORDER))
    story.append(Spacer(1, 12))

    story.append(Paragraph('Sentiment Overview', h2))
    kpi_rows = [['Brand','Articles','Positive %','Negative %','Neutral %']]
    for brand in brands:
        k = kpis.get(brand, {}); pos = k.get('positive',0); neg = k.get('negative',0)
        kpi_rows.append([Paragraph(f'<b>{brand}</b>', body), str(k.get('total',0)), f'{pos:.1f}%', f'{neg:.1f}%', f'{round(100-pos-neg,1):.1f}%'])
    t = Table(kpi_rows, colWidths=[4.5*cm,2.5*cm,3*cm,3*cm,3*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),WHITE),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
        ('ALIGN',(1,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,LIGHT]),('GRID',(0,0),(-1,-1),0.5,BORDER),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
    ]))
    story.append(t); story.append(Spacer(1, 12))

    if ab_test:
        story.append(Paragraph('A/B Statistical Test', h2))
        sig = 'Statistically significant difference (p < 0.05).' if ab_test.get('significant') else 'No significant difference at α = 0.05.'
        ab_rows = [['Metric','Value'],['T-statistic',str(ab_test.get('t_stat','—'))],['P-value',str(ab_test.get('p_value','—'))],['Conclusion',sig]]
        at = Table(ab_rows, colWidths=[5*cm,11*cm])
        at.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),WHITE),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,LIGHT]),
            ('GRID',(0,0),(-1,-1),0.5,BORDER),('TOPPADDING',(0,0),(-1,-1),6),
            ('BOTTOMPADDING',(0,0),(-1,-1),6),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
        ]))
        story.append(at); story.append(Spacer(1, 12))

    story.append(Paragraph('Top Articles by Confidence', h2))
    art_rows = [['Brand','Title','Sentiment','Conf.','Source']]
    for a in articles[:30]:
        title  = (a.get('title') or '')[:70] + ('…' if len(a.get('title',''))>70 else '')
        art_rows.append([Paragraph(a.get('brand',''),body), Paragraph(title,body),
                         a.get('sentiment',''), str(a.get('confidence','')), Paragraph((a.get('source') or '')[:20],body)])
    at2 = Table(art_rows, colWidths=[2.8*cm,7.5*cm,2.2*cm,1.5*cm,2.5*cm])
    s2 = [
        ('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),WHITE),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
        ('ALIGN',(2,1),(3,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('GRID',(0,0),(-1,-1),0.4,BORDER),('TOPPADDING',(0,0),(-1,-1),5),
        ('BOTTOMPADDING',(0,0),(-1,-1),5),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
    ]
    for i, a in enumerate(articles[:30], start=1):
        s = a.get('sentiment','')
        if s == 'POSITIVE': s2 += [('TEXTCOLOR',(2,i),(2,i),POS_CLR),('BACKGROUND',(2,i),(2,i),POS_BG)]
        elif s == 'NEGATIVE': s2 += [('TEXTCOLOR',(2,i),(2,i),NEG_CLR),('BACKGROUND',(2,i),(2,i),NEG_BG)]
        if i % 2 == 0: s2 += [('BACKGROUND',(0,i),(1,i),LIGHT),('BACKGROUND',(3,i),(-1,i),LIGHT)]
    at2.setStyle(TableStyle(s2))
    story.append(at2)
    story.append(Spacer(1,20))
    story.append(HRFlowable(width='100%', thickness=0.5, color=BORDER))
    story.append(Spacer(1,6))
    story.append(Paragraph('Generated by SentIQ · Brand Sentiment Intelligence Platform · Powered by DistilBERT & NewsAPI', ctr))

    doc.build(story)
    buffer.seek(0)
    filename = f"sentiq_report_{'_'.join(brands)}_{datetime.now().strftime('%Y%m%d')}.pdf"
    return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name=filename)





@app.route('/api/groq-test')
def groq_test():
    if not GROQ_API_KEY:
        return jsonify({'error': 'GROQ_API_KEY not set', 'key_set': False})
    try:
        response = http_requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": "Say hello in one sentence."}],
                "max_tokens": 50
            },
            timeout=20
        )
        return jsonify({
            'status': response.status_code,
            'key_prefix': GROQ_API_KEY[:8],
            'response': response.json() if response.status_code == 200 else response.text[:300]
        })
    except Exception as e:
        return jsonify({'error': str(e)})


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        print("Database ready.")
    scheduler.start()
    print("Alert scheduler started.")
    print("Starting SentIQ — open http://localhost:5000")
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
