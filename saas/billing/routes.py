import os
import stripe
import requests as _requests
from flask import Blueprint, request, session, redirect, url_for, jsonify, render_template
from supabase import create_client

billing_bp = Blueprint('billing', __name__)

stripe.api_key = os.getenv('STRIPE_SECRET_KEY', '')
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')

PRICE_IDS = {
    'creator_monthly': os.getenv('STRIPE_PRICE_CREATOR_MONTHLY', ''),
    'creator_annual':  os.getenv('STRIPE_PRICE_CREATOR_ANNUAL',  ''),
    'pro_monthly':     os.getenv('STRIPE_PRICE_PRO_MONTHLY',     ''),
    'pro_annual':      os.getenv('STRIPE_PRICE_PRO_ANNUAL',      ''),
    'studio_monthly':  os.getenv('STRIPE_PRICE_STUDIO_MONTHLY',  ''),
    'studio_annual':   os.getenv('STRIPE_PRICE_STUDIO_ANNUAL',   ''),
}

REEL_LIMITS = {'creator': 5, 'pro': 15, 'studio': 40}


def _sb_service():
    return create_client(
        os.getenv('SUPABASE_URL', ''),
        os.getenv('SUPABASE_SERVICE_KEY', ''),
    )


# ── Setup Trial ───────────────────────────────────────────────────────────────

@billing_bp.route('/setup-trial')
def setup_trial():
    plan    = request.args.get('plan', 'pro')
    billing = request.args.get('billing', 'monthly')
    user_id = session.get('user_id')

    if not user_id:
        return redirect(url_for('auth.login'))

    price_id = PRICE_IDS.get(f'{plan}_{billing}')
    if not price_id:
        return redirect('/?error=invalid_plan')

    checkout = stripe.checkout.Session.create(
        mode='subscription',
        payment_method_collection='always',   # card required — never bypass
        customer_email=None,                  # Stripe will collect email
        line_items=[{'price': price_id, 'quantity': 1}],
        subscription_data={
            'trial_period_days': 7,
            'metadata': {'user_id': user_id, 'plan': plan},
        },
        success_url=(
            url_for('billing.trial_success', _external=True)
            + '?session_id={CHECKOUT_SESSION_ID}'
        ),
        cancel_url=url_for('billing.trial_cancel', _external=True),
        metadata={'user_id': user_id, 'plan': plan},
    )

    return redirect(checkout.url)


@billing_bp.route('/trial/success')
def trial_success():
    # Stripe will fire customer.subscription.created webhook → DB update
    # We just show a welcome redirect here
    return redirect(url_for('dashboard') + '?welcome=1')


@billing_bp.route('/trial/cancel')
def trial_cancel():
    return redirect('/#pricing')


# ── Customer Portal ───────────────────────────────────────────────────────────

@billing_bp.route('/portal')
def portal():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login'))

    sb = _sb_service()
    profile = sb.table('profiles').select('stripe_customer_id').eq('user_id', user_id).single().execute()
    customer_id = profile.data.get('stripe_customer_id')

    if not customer_id:
        return redirect(url_for('dashboard'))

    portal_session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=url_for('dashboard', _external=True),
    )
    return redirect(portal_session.url)


# ── Stripe Webhooks ───────────────────────────────────────────────────────────

@billing_bp.route('/webhook', methods=['POST'])
def webhook():
    payload    = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return jsonify({'error': 'Invalid signature'}), 400

    handlers = {
        'customer.subscription.created':   _on_subscription_created,
        'customer.subscription.updated':    _on_subscription_updated,
        'customer.subscription.trial_will_end': _on_trial_will_end,
        'invoice.payment_failed':           _on_payment_failed,
        'customer.subscription.deleted':    _on_subscription_deleted,
    }

    handler = handlers.get(event['type'])
    if handler:
        try:
            handler(event['data']['object'])
        except Exception as e:
            # Log but return 200 so Stripe doesn't retry infinitely
            print(f'[webhook] {event["type"]} handler error: {e}')

    return jsonify({'status': 'ok'})


def _on_subscription_created(sub):
    sb      = _sb_service()
    meta    = sub.get('metadata', {})
    user_id = meta.get('user_id')
    plan    = meta.get('plan', 'pro')

    if not user_id:
        return

    sb.table('profiles').upsert({
        'user_id':                user_id,
        'plan':                   plan,
        'status':                 'trial',
        'stripe_subscription_id': sub['id'],
        'stripe_customer_id':     sub['customer'],
        'reel_limit':             REEL_LIMITS.get(plan, 5),
        'reels_used_this_month':  0,
        'trial_reels_used':       0,
    }).execute()

    _send_welcome_email(sub['customer'], plan)


def _send_welcome_email(customer_id: str, plan: str):
    resend_key = os.getenv('RESEND_API_KEY', '')
    if not resend_key:
        return
    try:
        customer = stripe.Customer.retrieve(customer_id)
        email    = customer.get('email', '')
    except Exception:
        return
    if not email:
        return

    plan_label  = plan.capitalize()
    reel_limit  = REEL_LIMITS.get(plan, 5)
    trial_limit = int(os.getenv('TRIAL_MAX_GENERATIONS', 3))

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#06020E; color:#fff; margin:0; padding:0; }}
    .wrap {{ max-width:560px; margin:40px auto; padding:40px 32px; background:#110820; border-radius:16px; border:1px solid #2a1a40; }}
    h1 {{ font-size:26px; margin:0 0 8px; background:linear-gradient(135deg,#B45EFF,#FF3CAC,#FF6B2B); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    p {{ color:#c8b8e8; line-height:1.6; margin:16px 0; }}
    .pill {{ display:inline-block; padding:4px 14px; border-radius:999px; font-size:13px; font-weight:600; background:rgba(180,94,255,0.15); border:1px solid rgba(180,94,255,0.4); color:#B45EFF; margin-bottom:8px; }}
    .cta {{ display:inline-block; margin:24px 0 0; padding:14px 32px; border-radius:9999px; background:linear-gradient(135deg,#B45EFF,#FF3CAC,#FF6B2B); color:#fff; font-weight:700; text-decoration:none; font-size:16px; }}
    .features {{ background:rgba(255,255,255,0.04); border-radius:10px; padding:20px 24px; margin:20px 0; }}
    .features li {{ color:#c8b8e8; margin:8px 0; list-style:none; padding:0; }}
    .features li::before {{ content:"✓ "; color:#B45EFF; font-weight:700; }}
    .footer {{ margin-top:32px; padding-top:24px; border-top:1px solid #2a1a40; font-size:12px; color:#6b5a8a; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="pill">{plan_label} Plan · 7-day trial</div>
    <h1>Welcome to DELULUREEL 🎬</h1>
    <p>Your free trial just started. Here's what you have:</p>
    <ul class="features">
      <li>{trial_limit} reels during the trial</li>
      <li>{reel_limit} reels/month after Day 7 ({plan_label} plan)</li>
      <li>AI scene direction synced to your BPM</li>
      <li>9:16 · 16:9 · 1:1 export formats</li>
      <li>Character consistency via Kling 3.0 Pro</li>
    </ul>
    <p>Upload your first track + photo and drop your reel in minutes.</p>
    <a href="https://delulureel.com/upload" class="cta">Create your first reel →</a>
    <div class="footer">
      DELULUREEL · Be delulu enough to drop your reel.<br>
      Your trial ends in 7 days. Cancel anytime: <a href="https://delulureel.com/billing/portal" style="color:#B45EFF;">manage subscription</a>
    </div>
  </div>
</body>
</html>
"""
    try:
        _requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {resend_key}',
                'Content-Type':  'application/json',
            },
            json={
                'from':    'DELULUREEL <hello@delulureel.com>',
                'to':      [email],
                'subject': '🎬 Welcome to DELULUREEL — your trial is live',
                'html':    html,
            },
            timeout=10,
        )
    except Exception:
        pass


def _on_subscription_updated(sub):
    sb          = _sb_service()
    customer_id = sub['customer']
    raw_status  = sub.get('status', '')

    status_map = {
        'active':   'active',
        'trialing': 'trial',
        'past_due': 'suspended',
        'canceled': 'cancelled',
    }
    status = status_map.get(raw_status, raw_status)

    sb.table('profiles').update({'status': status}).eq('stripe_customer_id', customer_id).execute()


def _on_trial_will_end(sub):
    resend_key = os.getenv('RESEND_API_KEY', '')
    if not resend_key:
        return

    customer_id = sub.get('customer')
    try:
        customer = stripe.Customer.retrieve(customer_id)
        email    = customer.get('email', '')
    except Exception:
        return

    if not email:
        return

    # Get plan name from Supabase for personalised copy
    sb = _sb_service()
    try:
        row  = sb.table('profiles').select('plan').eq('stripe_customer_id', customer_id).single().execute()
        plan = (row.data.get('plan') or 'pro').capitalize()
    except Exception:
        plan = 'Pro'

    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background:#06020E; color:#fff; margin:0; padding:0; }}
    .wrap {{ max-width:560px; margin:40px auto; padding:40px 32px; background:#110820; border-radius:16px; border:1px solid #2a1a40; }}
    h1 {{ font-size:24px; margin:0 0 8px; background:linear-gradient(135deg,#B45EFF,#FF3CAC,#FF6B2B); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    p {{ color:#c8b8e8; line-height:1.6; margin:16px 0; }}
    .cta {{ display:inline-block; margin:24px 0 0; padding:14px 32px; border-radius:9999px; background:linear-gradient(135deg,#B45EFF,#FF3CAC,#FF6B2B); color:#fff; font-weight:700; text-decoration:none; font-size:16px; }}
    .footer {{ margin-top:32px; padding-top:24px; border-top:1px solid #2a1a40; font-size:12px; color:#6b5a8a; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Your free trial ends in 2 days ✨</h1>
    <p>Hey! Your DELULUREEL <strong>{plan}</strong> trial is almost over — just 2 days left.</p>
    <p>After Day 7 your card will be charged automatically and you'll keep making reels without interruption. Cancel anytime from your dashboard before then and you won't be charged.</p>
    <p>If you haven't created your first reel yet, now's the moment:</p>
    <a href="https://delulureel.com/upload" class="cta">Create your reel →</a>
    <div class="footer">
      DELULUREEL · Be delulu enough to drop your reel.<br>
      To cancel: <a href="https://delulureel.com/billing/portal" style="color:#B45EFF;">manage subscription</a>
    </div>
  </div>
</body>
</html>
"""

    _requests.post(
        'https://api.resend.com/emails',
        headers={
            'Authorization': f'Bearer {resend_key}',
            'Content-Type': 'application/json',
        },
        json={
            'from':    'DELULUREEL <hello@delulureel.com>',
            'to':      [email],
            'subject': '⏰ Your free trial ends in 2 days',
            'html':    html,
        },
        timeout=10,
    )


def _on_payment_failed(invoice):
    sb          = _sb_service()
    customer_id = invoice.get('customer')
    sb.table('profiles').update({'status': 'suspended'}).eq('stripe_customer_id', customer_id).execute()


def _on_subscription_deleted(sub):
    sb          = _sb_service()
    customer_id = sub.get('customer')
    sb.table('profiles').update({'status': 'cancelled', 'plan': None}).eq('stripe_customer_id', customer_id).execute()
