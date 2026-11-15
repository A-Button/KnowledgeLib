(function (KL) {
  'use strict';

  // Collapse every whitespace run to one space while keeping a map from the
  // collapsed text back to the original string.  A quote copied out of Markdown
  // usually contains newlines and indentation that never reach the rendered DOM.
  function collapse(value) {
    var text = String(value == null ? '' : value);
    var out = '';
    var map = [];
    var pendingSpace = false;
    for (var i = 0; i < text.length; i += 1) {
      var ch = text.charAt(i);
      if (/\s/.test(ch)) {
        pendingSpace = true;
        continue;
      }
      if (pendingSpace && out) {
        out += ' ';
        map.push(i);
      }
      pendingSpace = false;
      out += ch;
      map.push(i);
    }
    return { text: out, map: map };
  }

  // Turn a Markdown quote into the plain text the reader actually renders:
  // no markers, no link targets, no list bullets, no fence lines.  Inline code
  // is protected first so stars/underscores inside code survive untouched.
  function plainMarkdown(value) {
    var text = String(value == null ? '' : value).replace(/\r\n?/g, '\n');
    var codes = [];

    text = text.replace(/```[^\n]*\n([\s\S]*?)\n?```/g, function (_, code) {
      return code;
    });
    text = text.replace(/`([^`\n]+)`/g, function (_, code) {
      var token = '@@HL' + codes.length + '@@';
      codes.push(code);
      return token;
    });
    text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
    text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
    text = text.replace(/^[ \t|:-]*-[ \t|:-]*$/gm, '');
    text = text.replace(/^[ \t]*#{1,6}[ \t]+/gm, '');
    text = text.replace(/^[ \t]*>[ \t]?/gm, '');
    text = text.replace(/^[ \t]*(?:[-*+]|\d+\.)[ \t]+/gm, '');
    text = text.replace(/^[ \t]*\[[ xX]\][ \t]+/gm, '');
    text = text.replace(/^[ \t]*(?:-{3,}|\*{3,})[ \t]*$/gm, '');
    text = text.replace(/\*\*([^*\n]+)\*\*/g, '$1');
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1$2');
    text = text.replace(/~~([^~\n]+)~~/g, '$1');
    text = text.replace(/<[^>]+>/g, '');
    text = text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"').replace(/&#39;/g, "'");
    text = text.replace(/@@HL(\d+)@@/g, function (_, index) {
      return codes[Number(index)] || '';
    });
    return text;
  }

  // Locate a quote inside flattened rendered text.  Returns offsets in the
  // original flat string, or null.  The Markdown-stripped form is a fallback so
  // quotes containing `code`, **bold**, [links](...) and line breaks still map
  // onto the DOM text produced by md.js.
  function locate(text, quote) {
    var source = collapse(text);
    var target = collapse(quote);
    if (!source.text || !target.text) return null;
    var index = source.text.indexOf(target.text);
    if (index < 0) {
      var plain = collapse(plainMarkdown(quote));
      if (plain.text && plain.text !== target.text) {
        target = plain;
        index = source.text.indexOf(target.text);
      }
    }
    if (index < 0) return null;
    var endIndex = index + target.text.length - 1;
    return {
      start: source.map[index],
      end: source.map[endIndex] + 1,
      length: target.text.length,
      plain: target.text
    };
  }

  function blockOf(node) {
    var el = node.parentNode;
    while (el && el.nodeType === 1) {
      if (/^(P|H1|H2|H3|H4|H5|H6|LI|BLOCKQUOTE|PRE|TD|TH)$/.test(el.nodeName)) return el;
      el = el.parentNode;
    }
    return null;
  }

  // Flatten rendered text into one string, inserting a space only where a block
  // boundary separates two text nodes.  Inline <code>/<strong> splits stay glued,
  // so "**bold**text" still reads as "boldtext" exactly like the DOM does.
  function collectText(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var flat = '';
    var segments = [];
    var previousBlock = null;
    var node = walker.nextNode();
    while (node) {
      var value = node.nodeValue || '';
      if (!value.trim()) {
        // md.js joins blocks with newline text nodes.  Keep a separating space
        // in the flattened text, but never mark the newline itself.
        if (flat && flat.charAt(flat.length - 1) !== ' ') flat += ' ';
        node = walker.nextNode();
        continue;
      }
      var block = blockOf(node);
      if (segments.length && block !== previousBlock && flat.charAt(flat.length - 1) !== ' ') flat += ' ';
      var start = flat.length;
      flat += value;
      segments.push({ node: node, start: start, end: flat.length });
      previousBlock = block;
      node = walker.nextNode();
    }
    return { flat: flat, segments: segments };
  }

  function insideMark(node) {
    var el = node.parentNode;
    return !!(el && el.closest && el.closest('mark.doc-mark'));
  }

  // Wrap every intersection of the quote with a text node.  This works across
  // inline <code>/<strong>/<a> boundaries where Range.surroundContents on one
  // text node would fail, and it never wraps across block elements.
  function apply(root, quote, note, scroll) {
    if (!root || !quote) return false;
    var data = collectText(root);
    var hit = locate(data.flat, quote);
    if (!hit) return false;

    var pieces = [];
    data.segments.forEach(function (segment) {
      var from = Math.max(hit.start, segment.start);
      var to = Math.min(hit.end, segment.end);
      if (from < to) pieces.push({ node: segment.node, from: from - segment.start, to: to - segment.start });
    });
    if (!pieces.length) return false;

    var firstMark = null;
    for (var i = pieces.length - 1; i >= 0; i -= 1) {
      var piece = pieces[i];
      if (!piece.node.parentNode || insideMark(piece.node)) continue;
      try {
        var range = document.createRange();
        range.setStart(piece.node, piece.from);
        range.setEnd(piece.node, piece.to);
        var mark = document.createElement('mark');
        mark.className = 'doc-mark';
        if (note) mark.title = note;
        range.surroundContents(mark);
        if (!firstMark) firstMark = mark;
      } catch (_) { /* the text node changed between collection and wrapping */ }
    }
    if (!firstMark) return false;
    if (scroll) firstMark.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return true;
  }

  KL.highlight = { collapse: collapse, plainMarkdown: plainMarkdown, locate: locate, apply: apply };
})(window.KL = window.KL || {});
