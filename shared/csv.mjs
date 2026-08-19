/** Shared CSV serialization and browser/server save helpers. */

export function buildCsv(rows, columns) {
  const lines = [columns.join(',')];
  for (const row of rows) {
    lines.push(columns.map(column => csvCell(row[column])).join(','));
  }
  return lines.join('\n');
}

export function csvCell(value) {
  const text = Array.isArray(value)
    ? value.join('|')
    : String(value ?? '');
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function safeFilenamePart(value) {
  return String(value).replace(/[^A-Za-z0-9_.-]+/g, '_') || 'participant';
}

export async function saveCsv({ rows, columns, filename, statusElement = null }) {
  const csv = buildCsv(rows, columns);
  if (statusElement) statusElement.textContent = 'Saving…';
  try {
    const response = await fetch('/api/save-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, csv }),
    });
    if (!response.ok) throw new Error(`server returned ${response.status}`);
    const info = await response.json();
    if (statusElement) statusElement.textContent = `Saved to ${info.path}`;
    return info;
  } catch (error) {
    downloadText(csv, filename, 'text/csv');
    if (statusElement) {
      statusElement.textContent = 'Server save failed; downloaded CSV instead.';
    }
    return { saved: false, downloaded: true, error: String(error) };
  }
}

export function downloadText(text, filename, type = 'text/plain') {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
