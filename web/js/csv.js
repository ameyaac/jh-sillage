/* CSV reading and writing, RFC 4180.
 *
 * The old page built CSV by wrapping fields in quotes with string interpolation,
 * which corrupts any value containing a quote or a comma — a brand like
 * 'Marc-Antoine Barrois "B683"' silently produced an unparseable file. Since
 * these files are the backup format, that mattered more than it looked.
 *
 * Exposed as window.Sillage.Csv (classic script, so the page still works when
 * opened straight off the filesystem — ES modules would be blocked by CORS).
 */
(function (root) {
  'use strict';

  var Sillage = root.Sillage = root.Sillage || {};

  function escapeField(value) {
    var s = (value === null || value === undefined) ? '' : String(value);
    return /["\n\r,]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  /* Rows are objects keyed by field name; `fields` fixes the column order. */
  function toCsv(fields, rows) {
    var lines = [fields.map(escapeField).join(',')];
    rows.forEach(function (row) {
      lines.push(fields.map(function (f) { return escapeField(row[f]); }).join(','));
    });
    return lines.join('\r\n') + '\r\n';
  }

  /* Text -> array of arrays. Handles quoted fields containing commas, newlines
   * and doubled quotes, and tolerates both CRLF and LF. */
  function parseRows(text) {
    if (typeof text !== 'string') return [];
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1); // strip BOM

    var rows = [], row = [], value = '', inQuotes = false, i = 0;

    while (i < text.length) {
      var ch = text[i];

      if (inQuotes) {
        if (ch === '"') {
          if (text[i + 1] === '"') { value += '"'; i += 2; continue; }
          inQuotes = false; i++; continue;
        }
        value += ch; i++; continue;
      }

      if (ch === '"') { inQuotes = true; i++; continue; }
      if (ch === ',') { row.push(value); value = ''; i++; continue; }
      if (ch === '\r' || ch === '\n') {
        row.push(value); value = '';
        rows.push(row); row = [];
        i += (ch === '\r' && text[i + 1] === '\n') ? 2 : 1;
        continue;
      }
      value += ch; i++;
    }

    if (value !== '' || row.length) { row.push(value); rows.push(row); }
    return rows.filter(function (r) {
      return r.some(function (cell) { return cell.trim() !== ''; });
    });
  }

  /* Text -> {header: [...], rows: [{field: value}]} */
  function parseObjects(text) {
    var rows = parseRows(text);
    if (!rows.length) return { header: [], rows: [] };
    var header = rows[0].map(function (h) { return h.trim(); });
    var out = rows.slice(1).map(function (cells) {
      var obj = {};
      header.forEach(function (name, idx) { obj[name] = cells[idx] === undefined ? '' : cells[idx]; });
      return obj;
    });
    return { header: header, rows: out };
  }

  Sillage.Csv = {
    escapeField: escapeField,
    toCsv: toCsv,
    parseRows: parseRows,
    parseObjects: parseObjects
  };
})(window);
