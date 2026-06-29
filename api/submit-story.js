export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', 'https://namecharted.com');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  const { name, story, attribution, year, _hp } = req.body || {};

  // Honeypot — bots fill this, humans don't
  if (_hp) return res.status(200).json({ success: true });

  if (!name || typeof name !== 'string') {
    return res.status(400).json({ error: 'Missing name' });
  }
  if (!story || typeof story !== 'string' || story.trim().length < 30) {
    return res.status(400).json({ error: 'Story is too short (30 characters minimum)' });
  }
  if (story.length > 1200) {
    return res.status(400).json({ error: 'Story is too long (1200 character limit)' });
  }

  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) {
    console.error('RESEND_API_KEY not configured');
    return res.status(500).json({ error: 'Server configuration error' });
  }

  const safeName = name.replace(/[<>"]/g, '').slice(0, 80);
  const safeStory = story.trim();
  const safeAttrib = (attribution || '').replace(/[<>"]/g, '').slice(0, 80);
  const safeYear = /^\d{4}$/.test(String(year)) ? String(year) : '';

  // Formatted for easy copy-paste into name_stories.json
  const jsonSnippet = JSON.stringify({
    text: safeStory,
    from: safeAttrib || undefined,
    year: safeYear ? Number(safeYear) : undefined,
  }, null, 2);

  const emailText = [
    `New name story submitted for: ${safeName}`,
    '',
    `Story:`,
    safeStory,
    '',
    `Attribution: ${safeAttrib || '(not provided)'}`,
    `Year: ${safeYear || '(not provided)'}`,
    '',
    '─'.repeat(40),
    'Copy-paste into data/name_stories.json:',
    '',
    `"${safeName.toLowerCase()}": [`,
    `  ${jsonSnippet.replace(/\n/g, '\n  ')}`,
    `]`,
  ].join('\n');

  const resp = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      from: 'NameCharted Stories <onboarding@resend.dev>',
      to: ['air.automate@gmail.com'],
      subject: `Name story submitted: ${safeName}`,
      text: emailText,
    }),
  });

  if (!resp.ok) {
    const err = await resp.text();
    console.error('Resend error:', err);
    return res.status(500).json({ error: 'Failed to submit — please try again' });
  }

  return res.status(200).json({ success: true });
}
