/**
 * Kwik Fat Cut chatbot endpoint (Cloudflare Pages Function, POST /api/chat).
 *
 * The API key is NEVER in the site code. Set it in the Cloudflare dashboard:
 *   Pages project > Settings > Environment variables (type: Secret)
 *     CHATBOT_API_KEY    the new key supplied by the client   (required)
 *     CHATBOT_PROVIDER   "anthropic" or "openai"               (default anthropic)
 *     CHATBOT_MODEL      model id for that provider            (required)
 * Then redeploy. Until CHATBOT_API_KEY and CHATBOT_MODEL are set this returns
 * 503 and the site shows "The assistant is being connected".
 */

const SYSTEM = [
  'You are the assistant on kwikfatcut.com, a free, non-profit health education',
  'platform grounded in Jamaican and Caribbean ancestral food knowledge.',
  'Principles: education before intervention, no body shaming, no pressure,',
  'nothing is sold. Always call the platform "Kwik Fat Cut" in full, never an',
  'abbreviation. Explain foods and nutrition plainly and kindly. You do not',
  'diagnose or prescribe; for symptoms, conditions or medication questions, say',
  'to speak with a qualified health professional. Keep answers short.'
].join(' ');

const MAX_MESSAGES = 12;
const MAX_CHARS = 600;

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status: status || 200,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
  });
}

function clean(messages) {
  if (!Array.isArray(messages)) return [];
  return messages
    .filter(m => m && (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string')
    .slice(-MAX_MESSAGES)
    .map(m => ({ role: m.role, content: m.content.slice(0, MAX_CHARS) }));
}

export async function onRequestPost({ request, env }) {
  if (!env.CHATBOT_API_KEY || !env.CHATBOT_MODEL) {
    return json({ error: 'not_configured' }, 503);
  }

  let body;
  try { body = await request.json(); } catch (e) { return json({ error: 'bad_request' }, 400); }
  const messages = clean(body.messages);
  if (!messages.length || messages[messages.length - 1].role !== 'user') {
    return json({ error: 'bad_request' }, 400);
  }

  const provider = (env.CHATBOT_PROVIDER || 'anthropic').toLowerCase();
  let reply = '';

  try {
    if (provider === 'openai') {
      const r = await fetch('https://api.openai.com/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env.CHATBOT_API_KEY },
        body: JSON.stringify({
          model: env.CHATBOT_MODEL,
          max_tokens: 500,
          messages: [{ role: 'system', content: SYSTEM }].concat(messages)
        })
      });
      if (!r.ok) return json({ error: 'upstream' }, 502);
      const d = await r.json();
      reply = d.choices && d.choices[0] && d.choices[0].message && d.choices[0].message.content || '';
    } else {
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': env.CHATBOT_API_KEY,
          'anthropic-version': '2023-06-01'
        },
        body: JSON.stringify({ model: env.CHATBOT_MODEL, max_tokens: 500, system: SYSTEM, messages })
      });
      if (!r.ok) return json({ error: 'upstream' }, 502);
      const d = await r.json();
      reply = (d.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n');
    }
  } catch (e) {
    return json({ error: 'upstream' }, 502);
  }

  return json({ reply: reply.trim() });
}

export function onRequest() {
  return json({ error: 'method_not_allowed' }, 405);
}
