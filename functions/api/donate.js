/**
 * Kwik Fat Cut donations (Cloudflare Pages Function).
 *
 *   POST /api/donate   {method:"paypal"|"wipay", amount, name, email}
 *                      Returns {url}. The support page sends the donor there.
 *   GET  /api/donate   WiPay sends the donor back here after checkout. The
 *                      payment is verified, then the donor lands on
 *                      /thank-you?gift=wipay, or back on /support with a note.
 *
 * Accounts are set in Cloudflare, never in the site code:
 *   Pages project > Settings > Variables and Secrets.
 *   Production and Preview have separate values, so test on Preview first.
 *
 *   PAYPAL_BUSINESS       PayPal account email or merchant ID that receives gifts
 *   PAYPAL_ENVIRONMENT    "live" (default) or "sandbox"
 *
 *   WIPAY_ENVIRONMENT     "live" or "sandbox". Required. Sandbox uses WiPay's
 *                         public test account, so no real money moves.
 *   WIPAY_ACCOUNT_NUMBER  WiPay business account number (required for live)
 *   WIPAY_API_KEY         WiPay API key, type Secret (required for live)
 *   WIPAY_COUNTRY         WiPay platform: JM (default), TT, BB, GY or GD
 *   WIPAY_FEE_STRUCTURE   merchant_absorb (default), customer_pay or split
 *
 *   DONATION_CURRENCY     USD (default) or JMD. Used by the PayPal route below only.
 *
 * WiPay gifts are always charged in Jamaican dollars (JMD), J$100 to J$1,000,000.
 * The support page labels WiPay as JMD and sends PayPal donors straight to the
 * Kwik Fat Cut PayPal pay link, so the PayPal route here is kept as a backup.
 *
 * A method with no account set returns 503, and the page offers the email
 * route instead, so nothing breaks before the accounts are connected.
 */

const SANDBOX_ACCOUNT = '1234567890'; // WiPay's published sandbox account
const SANDBOX_KEY = '123';            // WiPay's published sandbox API key
const MIN = 1;
const MAX = 100000;
const WIPAY_CURRENCY = 'JMD';
const WIPAY_MIN = 100;
const WIPAY_MAX = 1000000;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status: status || 200,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
  });
}

function redirect(url) {
  return new Response(null, { status: 303, headers: { Location: url, 'Cache-Control': 'no-store' } });
}

function currency(env) {
  const c = String(env.DONATION_CURRENCY || 'USD').toUpperCase();
  return /^[A-Z]{3}$/.test(c) ? c : 'USD';
}

function wipay(env) {
  const mode = String(env.WIPAY_ENVIRONMENT || '').toLowerCase();
  if (mode !== 'live' && mode !== 'sandbox') return null;
  const sandbox = mode === 'sandbox';
  const account = String(env.WIPAY_ACCOUNT_NUMBER || (sandbox ? SANDBOX_ACCOUNT : '')).trim();
  const key = String(env.WIPAY_API_KEY || (sandbox ? SANDBOX_KEY : '')).trim();
  if (!/^\d+$/.test(account) || !key) return null;
  const country = String(env.WIPAY_COUNTRY || 'JM').toUpperCase();
  return {
    mode, account, key,
    country: /^(JM|TT|BB|GY|GD)$/.test(country) ? country : 'JM',
    fees: /^(merchant_absorb|customer_pay|split)$/.test(env.WIPAY_FEE_STRUCTURE || '') ? env.WIPAY_FEE_STRUCTURE : 'merchant_absorb'
  };
}

function paypal(env) {
  const business = String(env.PAYPAL_BUSINESS || '').trim();
  if (!business) return null;
  return { business, sandbox: String(env.PAYPAL_ENVIRONMENT || '').toLowerCase() === 'sandbox' };
}

function orderId() {
  const rand = Array.from(crypto.getRandomValues(new Uint8Array(3)), b => b.toString(36).padStart(2, '0')).join('').slice(0, 4);
  return ('KFC' + Date.now().toString(36) + rand).slice(0, 16);
}

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch (e) { return json({ error: 'bad_request' }, 400); }

  const method = String(body.method || '').toLowerCase();
  const amount = Math.round(Number(body.amount) * 100) / 100;
  const name = String(body.name || '').replace(/[\u0000-\u001f<>]/g, '').trim().slice(0, 60);
  const email = String(body.email || '').trim().slice(0, 50);
  const min = method === 'wipay' ? WIPAY_MIN : MIN;
  const max = method === 'wipay' ? WIPAY_MAX : MAX;
  if (!(amount >= min && amount <= max) || name.length < 2 || !EMAIL.test(email)) {
    return json({ error: 'bad_request' }, 400);
  }
  const total = amount.toFixed(2);
  const cur = method === 'wipay' ? WIPAY_CURRENCY : currency(env);
  const site = new URL(request.url).origin;

  if (method === 'paypal') {
    const pp = paypal(env);
    if (!pp) return json({ error: 'not_configured' }, 503);
    const q = new URLSearchParams({
      business: pp.business,
      amount: total,
      currency_code: cur,
      item_name: 'Donation to Kwik Fat Cut',
      no_recurring: '0',
      return: site + '/thank-you?gift=paypal',
      cancel_return: site + '/support?gift=cancelled'
    });
    const base = pp.sandbox ? 'https://www.sandbox.paypal.com/donate' : 'https://www.paypal.com/donate';
    return json({ url: base + '?' + q.toString() });
  }

  if (method === 'wipay') {
    const wp = wipay(env);
    if (!wp) return json({ error: 'not_configured' }, 503);
    const payload = {
      account_number: wp.account,
      country_code: wp.country,
      currency: cur,
      environment: wp.mode,
      fee_structure: wp.fees,
      method: 'credit_card_co',
      order_id: orderId(),
      origin: 'KwikFatCut',
      response_url: site + '/api/donate',
      total,
      email,
      name,
      data: JSON.stringify({ total }),
      version: '1.0.0'
    };
    try {
      const r = await fetch('https://' + wp.country.toLowerCase() + '.wipayfinancial.com/plugins/payments/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(payload)
      });
      const d = await r.json().catch(() => null);
      if (r.ok && d && typeof d.url === 'string' && /^https:\/\//.test(d.url)) {
        return json({ url: d.url });
      }
      console.log('WiPay request refused', r.status, d && d.message);
      return json({ error: 'upstream' }, 502);
    } catch (e) {
      console.log('WiPay request failed', String(e));
      return json({ error: 'upstream' }, 502);
    }
  }

  return json({ error: 'bad_request' }, 400);
}

// WiPay returns the donor here. The hash proves the result came from WiPay:
// md5(transaction_id + original total + API key), sent for successful payments only.
export async function onRequestGet({ request, env }) {
  const q = new URL(request.url).searchParams;
  const wp = wipay(env);
  const status = String(q.get('status') || '').toLowerCase();
  const txn = String(q.get('transaction_id') || '');
  const hash = String(q.get('hash') || '').toLowerCase();

  if (status !== 'success') return redirect('/support?gift=failed');

  // The original total travels in "data". If WiPay ever drops it, the returned
  // total matches the original when the merchant absorbs the fee.
  const totals = [];
  try { totals.push(String(JSON.parse(q.get('data') || '{}').total || '')); } catch (e) {}
  if (wp && wp.fees === 'merchant_absorb' && Number(q.get('total')) > 0) totals.push(Number(q.get('total')).toFixed(2));

  const verified = wp && txn && hash && totals.some(t => /^\d+\.\d{2}$/.test(t) && md5(txn + t + wp.key) === hash);
  if (verified) return redirect('/thank-you?gift=wipay');
  console.log('WiPay return could not be verified', txn);
  return redirect('/support?gift=unverified');
}

export function onRequest() {
  return json({ error: 'method_not_allowed' }, 405);
}

// Compact MD5 (RFC 1321) over UTF-8, so the check runs the same in any runtime.
function md5(str) {
  const bytes = new TextEncoder().encode(str);
  const n = ((bytes.length + 8) >>> 6) + 1;
  const words = new Uint32Array(n * 16);
  for (let i = 0; i < bytes.length; i++) words[i >> 2] |= bytes[i] << ((i % 4) * 8);
  words[bytes.length >> 2] |= 0x80 << ((bytes.length % 4) * 8);
  words[n * 16 - 2] = bytes.length * 8;
  const S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
  const K = new Uint32Array(64);
  for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296);
  let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
  for (let o = 0; o < words.length; o += 16) {
    let a = a0, b = b0, c = c0, d = d0;
    for (let i = 0; i < 64; i++) {
      let f, g;
      if (i < 16) { f = (b & c) | (~b & d); g = i; }
      else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16; }
      else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16; }
      else { f = c ^ (b | ~d); g = (7 * i) % 16; }
      const s = S[(i >> 4) * 4 + (i % 4)];
      const t = d; d = c; c = b;
      const x = (a + f + K[i] + words[o + g]) >>> 0;
      b = (b + ((x << s) | (x >>> (32 - s)))) >>> 0;
      a = t;
    }
    a0 = (a0 + a) >>> 0; b0 = (b0 + b) >>> 0; c0 = (c0 + c) >>> 0; d0 = (d0 + d) >>> 0;
  }
  return [a0, b0, c0, d0].map(v => Array.from({ length: 4 }, (_, i) => ((v >>> (i * 8)) & 255).toString(16).padStart(2, '0')).join('')).join('');
}

export { md5 as _md5 };
