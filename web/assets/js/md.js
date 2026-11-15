(function (KL) {
  'use strict';

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/\n/g, ' ');
  }

  function unescapeUrl(value) {
    return String(value).replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
  }

  function safeUrl(raw) {
    var url = unescapeUrl(String(raw || '').trim());
    if (!url) return '';
    if (/^https?:\/\//i.test(url)) return url;
    if (/^#/.test(url)) return url;
    if (/\.md$/i.test(url) && !/^[a-z]+:/i.test(url)) return url;
    return '';
  }

  function inline(raw) {
    var text = escapeHtml(raw);
    var codes = [];
    var cites = [];

    text = text.replace(/\x60([^\x60]+)\x60/g, function (_, code) {
      var token = '@@IC' + codes.length + '@@';
      codes.push('<code>' + code + '</code>');
      return token;
    });

    text = text.replace(/\[([KW])(\d+)\]/g, function (_, kind, number) {
      var ref = kind + number;
      var token = '@@CT' + cites.length + '@@';
      cites.push('<button type="button" class="cite" data-cite="' + ref + '" title="查看来源 ' + ref + '">' + ref + '</button>');
      return token;
    });

    text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (match, label, href) {
      var safe = safeUrl(href);
      if (!safe) return label;
      if (/\.md$/i.test(safe) && !/^https?:/i.test(safe)) {
        return '<a href="#" data-file="' + escapeAttr(decodeURIComponent(safe)) + '">' + label + '</a>';
      }
      return '<a href="' + escapeAttr(safe) + '" target="_blank" rel="noreferrer noopener">' + label + '</a>';
    });

    text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    text = text.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');

    text = text.replace(/@@CT(\d+)@@/g, function (_, index) { return cites[Number(index)] || ''; });
    text = text.replace(/@@IC(\d+)@@/g, function (_, index) { return codes[Number(index)] || ''; });
    return text;
  }

  function splitRow(line) {
    var value = line.trim();
    if (value.charAt(0) === '|') value = value.slice(1);
    if (value.charAt(value.length - 1) === '|') value = value.slice(0, -1);
    return value.split('|').map(function (cell) { return cell.trim(); });
  }

  function isSeparator(line) {
    var cells = splitRow(line);
    return cells.length > 0 && cells.every(function (cell) { return /^:?-{3,}:?$/.test(cell); });
  }

  function isBlockStart(line) {
    return /^(#{1,6})\s+/.test(line)
      || /^\s*(\x60{3,}|~{3,})/.test(line)
      || /^\s*>/.test(line)
      || /^\s*([-*+]|\d+\.)\s+/.test(line)
      || /^\s*---\s*$/.test(line);
  }

  function tableAt(lines, index) {
    if (index + 1 >= lines.length) return false;
    if (lines[index].indexOf('|') < 0) return false;
    if (lines[index + 1].indexOf('|') < 0) return false;
    return isSeparator(lines[index + 1]);
  }

  function renderTable(lines, start) {
    var header = splitRow(lines[start]);
    var rows = [];
    var index = start + 2;
    while (index < lines.length && lines[index].trim() && lines[index].indexOf('|') >= 0) {
      rows.push(splitRow(lines[index]));
      index += 1;
    }
    var html = '<table><thead><tr>';
    header.forEach(function (cell) { html += '<th>' + inline(cell) + '</th>'; });
    html += '</tr></thead><tbody>';
    rows.forEach(function (row) {
      html += '<tr>';
      for (var i = 0; i < header.length; i += 1) {
        html += '<td>' + inline(row[i] == null ? '' : row[i]) + '</td>';
      }
      html += '</tr>';
    });
    html += '</tbody></table>';
    return { html: html, next: index };
  }

  function renderList(lines, start) {
    var ordered = /^\s*\d+\.\s+/.test(lines[start]);
    var html = ordered ? '<ol>' : '<ul>';
    var index = start;
    var pattern = ordered ? /^\s*\d+\.\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
    while (index < lines.length) {
      var match = lines[index].match(pattern);
      if (!match) break;
      var content = match[1];
      var box = '';
      var check = content.match(/^\[([ xX])\]\s+(.*)$/);
      if (check) {
        box = '<input type="checkbox" disabled' + (check[1].toLowerCase() === 'x' ? ' checked' : '') + '> ';
        content = check[2];
      }
      html += '<li>' + box + inline(content) + '</li>';
      index += 1;
    }
    html += ordered ? '</ol>' : '</ul>';
    return { html: html, next: index };
  }

  function render(source) {
    var text = String(source == null ? '' : source).replace(/\r\n?/g, '\n');
    var lines = text.split('\n');
    var out = [];
    var index = 0;

    while (index < lines.length) {
      var line = lines[index];

      if (!line.trim()) { index += 1; continue; }

      var fence = line.match(/^\s*(\x60{3,}|~{3,})\s*([\w-]*)\s*$/);
      if (fence) {
        var marker = fence[1].charAt(0);
        var lang = fence[2] || '';
        var buffer = [];
        index += 1;
        var closer = new RegExp('^\\s*' + marker + '{3,}\\s*$');
        while (index < lines.length && !closer.test(lines[index])) {
          buffer.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        out.push('<pre><code' + (lang ? ' class="lang-' + escapeAttr(lang) + '"' : '') + '>' + escapeHtml(buffer.join('\n')) + '</code></pre>');
        continue;
      }

      if (tableAt(lines, index)) {
        var table = renderTable(lines, index);
        out.push(table.html);
        index = table.next;
        continue;
      }

      var heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        var level = heading[1].length;
        out.push('<h' + level + '>' + inline(heading[2]) + '</h' + level + '>');
        index += 1;
        continue;
      }

      if (/^\s*---\s*$/.test(line)) {
        out.push('<hr>');
        index += 1;
        continue;
      }

      if (/^\s*>/.test(line)) {
        var quote = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          quote.push(lines[index].replace(/^\s*>\s?/, ''));
          index += 1;
        }
        out.push('<blockquote>' + render(quote.join('\n')) + '</blockquote>');
        continue;
      }

      if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
        var list = renderList(lines, index);
        out.push(list.html);
        index = list.next;
        continue;
      }

      var paragraph = [];
      while (index < lines.length && lines[index].trim()) {
        if (paragraph.length && (isBlockStart(lines[index]) || tableAt(lines, index))) break;
        paragraph.push(lines[index]);
        index += 1;
      }
      if (!paragraph.length) {
        paragraph.push(lines[index]);
        index += 1;
      }
      out.push('<p>' + inline(paragraph.join(' ')) + '</p>');
    }

    return out.join('\n');
  }

  KL.md = { render: render, escape: escapeHtml };
})(window.KL = window.KL || {});
